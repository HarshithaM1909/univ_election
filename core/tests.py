from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.management.commands.merge_election_results import Command as MergeCommand
from core.models import Candidate, Student, University, Vote


class CandidateUniqueDepartmentTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(name="Test U", university_id="TESTU")

    def test_duplicate_department_rejected(self):
        Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Candidate.objects.create(university=self.university, name="Bob", photo_url="http://x/b.png", forum="CS")

    def test_same_department_different_university_allowed(self):
        other = University.objects.create(name="Other U", university_id="OTHERU")
        Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        Candidate.objects.create(university=other, name="Carol", photo_url="http://x/c.png", forum="CS")
        self.assertEqual(Candidate.objects.count(), 2)


class ExportBallotsViewTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(name="Test U", university_id="TESTU")
        self.candidate = Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        self.student = Student.objects.create(university=self.university, student_id="S1", has_voted=True)
        Vote.objects.create(university=self.university, student=self.student, candidate=self.candidate)
        self.candidate.vote_count = 1
        self.candidate.save(update_fields=['vote_count'])

    def test_forbidden_without_token(self):
        response = self.client.get(reverse('export_ballots_api'), {'university_id': 'TESTU'})
        self.assertEqual(response.status_code, 403)

    @override_settings(MERGE_API_TOKEN='secret-token')
    def test_forbidden_with_wrong_token(self):
        response = self.client.get(
            reverse('export_ballots_api'), {'university_id': 'TESTU'},
            HTTP_AUTHORIZATION='Bearer wrong-token',
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(MERGE_API_TOKEN='secret-token')
    def test_ok_with_correct_token(self):
        response = self.client.get(
            reverse('export_ballots_api'), {'university_id': 'TESTU'},
            HTTP_AUTHORIZATION='Bearer secret-token',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['university_id'], 'TESTU')
        self.assertEqual(data['candidates'], [{'forum': 'CS', 'name': 'Alice', 'vote_count': 1}])
        self.assertEqual(data['students'], [{'student_id': 'S1', 'has_voted': True}])
        self.assertEqual(len(data['votes']), 1)
        self.assertEqual(data['votes'][0]['student_id'], 'S1')
        self.assertEqual(data['votes'][0]['candidate_forum'], 'CS')

    @override_settings(MERGE_API_TOKEN='secret-token')
    def test_missing_university_id(self):
        response = self.client.get(reverse('export_ballots_api'), HTTP_AUTHORIZATION='Bearer secret-token')
        self.assertEqual(response.status_code, 400)

    @override_settings(MERGE_API_TOKEN='secret-token')
    def test_unknown_university_id(self):
        response = self.client.get(
            reverse('export_ballots_api'), {'university_id': 'NOPE'},
            HTTP_AUTHORIZATION='Bearer secret-token',
        )
        self.assertEqual(response.status_code, 404)


class MergeElectionResultsDiffWriteTests(TestCase):
    """
    Exercises the merge command's diff/write logic directly (bypassing the
    network fetch), simulating a remote payload as if it came from a second
    independent system.
    """

    def setUp(self):
        self.university = University.objects.create(name="Test U", university_id="TESTU")
        self.cs = Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        self.ee = Candidate.objects.create(university=self.university, name="Bob", photo_url="http://x/b.png", forum="EE")

        # Voted only locally -> should be untouched by merge.
        self.local_only = Student.objects.create(university=self.university, student_id="LOCAL1", has_voted=True)
        Vote.objects.create(university=self.university, student=self.local_only, candidate=self.cs)

        # Not yet voted anywhere locally, voted remotely -> importable.
        self.importable = Student.objects.create(university=self.university, student_id="IMPORT1", has_voted=False)

        # Voted on both systems -> conflict, must not be imported.
        self.conflict = Student.objects.create(university=self.university, student_id="CONFLICT1", has_voted=True)
        Vote.objects.create(university=self.university, student=self.conflict, candidate=self.cs)

        # Exists remotely but not locally at all -> reported, not imported.
        # (No local Student row created for "GHOST1".)

        now = timezone.now().isoformat()
        self.payload = {
            'university_id': 'TESTU',
            'university_name': 'Test U',
            'nota_votes': 3,
            'candidates': [
                {'forum': 'CS', 'name': 'Alice', 'vote_count': 1},
                {'forum': 'EE', 'name': 'Bob', 'vote_count': 0},
                {'forum': 'ME', 'name': 'Zed', 'vote_count': 1},  # unmatched department locally
            ],
            'students': [
                {'student_id': 'IMPORT1', 'has_voted': True},
                {'student_id': 'CONFLICT1', 'has_voted': True},
                {'student_id': 'GHOST1', 'has_voted': True},
                {'student_id': 'LOCAL1', 'has_voted': False},
            ],
            'votes': [
                {'student_id': 'IMPORT1', 'candidate_forum': 'EE', 'timestamp': now},
                {'student_id': 'CONFLICT1', 'candidate_forum': 'EE', 'timestamp': now},
                {'student_id': 'GHOST1', 'candidate_forum': 'CS', 'timestamp': now},
                {'student_id': 'IMPORT1', 'candidate_forum': 'ME', 'timestamp': now},
            ],
        }

    def test_diff_classifies_correctly(self):
        cmd = MergeCommand()
        report = cmd._diff(self.university, self.payload, strict=False)

        self.assertEqual(report['unmatched_forums'], ['ME'])
        self.assertEqual(set(report['importable'].keys()), {'IMPORT1'})
        self.assertEqual(report['students_not_found_locally'], ['GHOST1'])
        self.assertEqual(report['votes_skipped_unmatched_candidate'], [('IMPORT1', 'ME')])

        self.assertEqual(len(report['conflicts']), 1)
        conflict = report['conflicts'][0]
        self.assertEqual(conflict['student_id'], 'CONFLICT1')
        self.assertFalse(conflict['identical'])  # local voted CS, remote voted EE

    def test_diff_raises_on_strict_unmatched(self):
        cmd = MergeCommand()
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            cmd._diff(self.university, self.payload, strict=True)

    def test_write_applies_only_importable_and_recomputes_counts(self):
        cmd = MergeCommand()
        report = cmd._diff(self.university, self.payload, strict=False)
        cmd._write(self.university, report, apply_changes=True)

        self.importable.refresh_from_db()
        self.assertTrue(self.importable.has_voted)
        self.assertTrue(Vote.objects.filter(student=self.importable, candidate=self.ee).exists())

        # Conflict student must remain untouched: still only their original local vote.
        self.assertEqual(Vote.objects.filter(student=self.conflict).count(), 1)
        self.assertTrue(Vote.objects.filter(student=self.conflict, candidate=self.cs).exists())

        self.cs.refresh_from_db()
        self.ee.refresh_from_db()
        self.assertEqual(self.cs.vote_count, Vote.objects.filter(candidate=self.cs).count())
        self.assertEqual(self.ee.vote_count, Vote.objects.filter(candidate=self.ee).count())
        self.assertEqual(self.ee.vote_count, 1)

    def test_write_is_idempotent(self):
        cmd = MergeCommand()
        report1 = cmd._diff(self.university, self.payload, strict=False)
        cmd._write(self.university, report1, apply_changes=True)

        report2 = cmd._diff(self.university, self.payload, strict=False)
        cmd._write(self.university, report2, apply_changes=True)

        self.assertEqual(Vote.objects.filter(student=self.importable, candidate=self.ee).count(), 1)
        self.ee.refresh_from_db()
        self.assertEqual(self.ee.vote_count, 1)
