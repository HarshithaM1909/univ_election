from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.management.commands.merge_election_results import Command as MergeCommand
from core.models import Candidate, Profile, Student, University, Vote
from core.views import get_live_nota_votes


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


class VotingFlowNotaTests(TestCase):
    """
    Exercises the real voter_kiosk -> voting_page HTTP flow and checks that
    get_live_nota_votes always matches: 10 * (voted students) - (candidate votes).
    """

    def setUp(self):
        # core.0002 is a data migration that seeds a "Default University"
        # (pk=1). Anonymous kiosk requests resolve their university via
        # get_current_university() -> University.objects.first(), so tests
        # that go through the real kiosk views must use that seeded row
        # rather than a separately created one it wouldn't resolve to.
        self.university = University.objects.get(university_id='DEFAULT')
        self.cs = Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        self.ee = Candidate.objects.create(university=self.university, name="Bob", photo_url="http://x/b.png", forum="EE")

    def _cast_vote(self, student_id, candidate_pks, nota=False):
        client = Client()
        auth_response = client.post(reverse('voter_kiosk'), {'student_id': student_id})
        self.assertEqual(auth_response.status_code, 302)
        values = [str(pk) for pk in candidate_pks]
        if nota:
            values.append('nota')
        return client.post(reverse('voting_page'), {'candidate_pks': values})

    def test_full_nota_ballot_zero_candidates(self):
        student = Student.objects.create(university=self.university, student_id='NOTA1')
        response = self._cast_vote('NOTA1', [], nota=True)
        self.assertRedirects(response, reverse('thank_you'))

        student.refresh_from_db()
        self.assertTrue(student.has_voted)
        self.assertEqual(Vote.objects.filter(student=student).count(), 0)
        self.assertEqual(get_live_nota_votes(self.university), 10)

    def test_partial_candidates_plus_nota(self):
        student = Student.objects.create(university=self.university, student_id='PARTIAL1')
        response = self._cast_vote('PARTIAL1', [self.cs.pk, self.ee.pk], nota=True)
        self.assertRedirects(response, reverse('thank_you'))

        student.refresh_from_db()
        self.assertTrue(student.has_voted)
        self.assertEqual(Vote.objects.filter(student=student).count(), 2)
        self.assertEqual(get_live_nota_votes(self.university), 8)  # 10 - 2 candidates

    def test_partial_ballot_without_nota_is_rejected(self):
        student = Student.objects.create(university=self.university, student_id='REJECT1')
        response = self._cast_vote('REJECT1', [self.cs.pk], nota=False)

        self.assertEqual(response.status_code, 200)  # re-rendered with a validation error
        student.refresh_from_db()
        self.assertFalse(student.has_voted)
        self.assertEqual(get_live_nota_votes(self.university), 0)

    def test_more_than_ten_candidates_rejected(self):
        student = Student.objects.create(university=self.university, student_id='TOOMANY1')
        candidates = [
            Candidate.objects.create(
                university=self.university, name=f"Cand{i}", photo_url="http://x/c.png", forum=f"DEPT{i}"
            )
            for i in range(11)
        ]
        response = self._cast_vote('TOOMANY1', [c.pk for c in candidates])

        self.assertEqual(response.status_code, 200)
        student.refresh_from_db()
        self.assertFalse(student.has_voted)

    def test_live_nota_aggregates_across_multiple_voters(self):
        Student.objects.create(university=self.university, student_id='MULTI1')
        Student.objects.create(university=self.university, student_id='MULTI2')
        self._cast_vote('MULTI1', [self.cs.pk], nota=True)          # contributes 9
        self._cast_vote('MULTI2', [], nota=True)                    # contributes 10
        self.assertEqual(get_live_nota_votes(self.university), 19)

    def test_no_votes_cast_means_zero_nota(self):
        self.assertEqual(get_live_nota_votes(self.university), 0)


class DashboardLiveNotaTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(name="Dash U", university_id="DASHU")
        self.candidate = Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        self.officer = User.objects.create_superuser('dashofficer', 'o@x.com', 'pass12345')
        Profile.objects.create(user=self.officer, university=self.university)

        voter = Student.objects.create(university=self.university, student_id='V1', has_voted=True)
        Vote.objects.create(university=self.university, student=voter, candidate=self.candidate)
        # V1 used 1 of their 10 votes on a candidate -> the other 9 are implicitly NOTA.
        self.expected_nota = get_live_nota_votes(self.university)

    def test_dashboard_view_shows_live_nota(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['nota_votes'], self.expected_nota)
        self.assertEqual(response.context['nota_votes'], 9)

    def test_dashboard_api_shows_live_nota(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse('dashboard_api'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['nota_votes'], self.expected_nota)
        self.assertEqual(data['total_votes_cast'], 1)

    def test_dashboard_requires_officer_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertNotEqual(response.status_code, 200)


class MergeFullNotaVoterTests(TestCase):
    """
    Regression coverage for the bug where a student who cast a full-NOTA
    ballot (0 candidate votes) on the remote system was silently dropped by
    the merge instead of being imported as has_voted=True locally.
    """

    def setUp(self):
        self.university = University.objects.create(name="Merge Nota U", university_id="MERGENOTAU")
        self.cs = Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        self.phy_student = Student.objects.create(university=self.university, student_id='PHYONLY1', has_voted=False)
        self.nota_student = Student.objects.create(university=self.university, student_id='FULLNOTA1', has_voted=False)

        now = timezone.now().isoformat()
        self.payload = {
            'university_id': 'MERGENOTAU',
            'university_name': 'Merge Nota U (remote)',
            'nota_votes': 999,  # deliberately wrong, must be ignored by the live calculation
            'candidates': [
                {'forum': 'CS', 'name': 'Alice', 'vote_count': 0},
                {'forum': 'PHY', 'name': 'Zed', 'vote_count': 1},  # no local match
            ],
            'students': [
                {'student_id': 'FULLNOTA1', 'has_voted': True},   # 0 candidate votes remotely
                {'student_id': 'PHYONLY1', 'has_voted': True},    # only vote is for an unmatched dept
            ],
            'votes': [
                {'student_id': 'PHYONLY1', 'candidate_forum': 'PHY', 'timestamp': now},
                # FULLNOTA1 has no vote rows at all: that's the full-NOTA case.
            ],
        }

    def test_full_nota_voter_is_importable(self):
        cmd = MergeCommand()
        report = cmd._diff(self.university, self.payload, strict=False)
        self.assertIn('FULLNOTA1', report['importable'])
        self.assertEqual(report['importable']['FULLNOTA1'][1], [])  # zero staged candidate votes

    def test_unmatched_department_only_voter_is_still_importable(self):
        cmd = MergeCommand()
        report = cmd._diff(self.university, self.payload, strict=False)
        self.assertIn('PHYONLY1', report['importable'])
        self.assertEqual(report['votes_skipped_unmatched_candidate'], [('PHYONLY1', 'PHY')])

    def test_write_marks_full_nota_voter_as_voted_with_no_vote_rows(self):
        cmd = MergeCommand()
        report = cmd._diff(self.university, self.payload, strict=False)
        cmd._write(self.university, report, apply_changes=True)

        self.nota_student.refresh_from_db()
        self.phy_student.refresh_from_db()
        self.assertTrue(self.nota_student.has_voted)
        self.assertTrue(self.phy_student.has_voted)
        self.assertEqual(Vote.objects.filter(student=self.nota_student).count(), 0)
        self.assertEqual(Vote.objects.filter(student=self.phy_student).count(), 0)

    def test_live_nota_after_merge_counts_both_full_nota_voters(self):
        cmd = MergeCommand()
        report = cmd._diff(self.university, self.payload, strict=False)
        with transaction.atomic():
            cmd._write(self.university, report, apply_changes=True)
            live_nota = get_live_nota_votes(self.university)
        # 2 students voted, 0 candidate votes cast between them -> 10*2 - 0 = 20,
        # regardless of the remote's (deliberately wrong) reported nota_votes: 999.
        self.assertEqual(live_nota, 20)


class ExportBallotsLiveNotaTests(TestCase):
    def setUp(self):
        self.university = University.objects.create(name="Export Nota U", university_id="EXPORTNOTAU")
        self.candidate = Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        student = Student.objects.create(university=self.university, student_id='E1', has_voted=True)
        Vote.objects.create(university=self.university, student=student, candidate=self.candidate)

    @override_settings(MERGE_API_TOKEN='secret-token')
    def test_export_reports_live_nota_not_stored_counter(self):
        response = self.client.get(
            reverse('export_ballots_api'), {'university_id': 'EXPORTNOTAU'},
            HTTP_AUTHORIZATION='Bearer secret-token',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['nota_votes'], get_live_nota_votes(self.university))
        self.assertEqual(data['nota_votes'], 9)  # 1 student, 1 candidate vote -> 10*1 - 1


class ResultsRevealSmokeTest(TestCase):
    @override_settings(RESULTS_ACCESS_TOKEN='reveal-token')
    def test_results_reveal_renders_for_authorized_officer(self):
        university = University.objects.create(name="Results U", university_id="RESULTSU")
        Candidate.objects.create(university=university, name="Alice", photo_url="http://x/a.png", forum="CS")
        officer = User.objects.create_superuser('resultsofficer', 'o@x.com', 'pass12345')
        Profile.objects.create(user=officer, university=university)

        self.client.force_login(officer)
        response = self.client.get(reverse('results_reveal', kwargs={'token': 'reveal-token'}))
        self.assertEqual(response.status_code, 200)


class ResetElectionTests(TestCase):
    """
    Covers the out-of-sync scenario that motivated this feature: a student
    marked has_voted=True with no corresponding Vote rows (e.g. because votes
    were cleared by hand without also resetting has_voted) permanently
    inflates get_live_nota_votes. Reset must clear both together.
    """

    def setUp(self):
        self.university = University.objects.create(name="Reset U", university_id="RESETU")
        self.candidate = Candidate.objects.create(university=self.university, name="Alice", photo_url="http://x/a.png", forum="CS")
        self.officer = User.objects.create_superuser('resetofficer', 'o@x.com', 'pass12345')
        Profile.objects.create(user=self.officer, university=self.university)

        self.voted_with_ballot = Student.objects.create(university=self.university, student_id='R1', has_voted=True)
        Vote.objects.create(university=self.university, student=self.voted_with_ballot, candidate=self.candidate)
        self.candidate.vote_count = 1
        self.candidate.save(update_fields=['vote_count'])

        # Orphaned state: has_voted=True but no Vote rows, as if votes were
        # deleted by hand without resetting the flag.
        self.orphaned_voter = Student.objects.create(university=self.university, student_id='R2', has_voted=True)

        self.not_voted = Student.objects.create(university=self.university, student_id='R3', has_voted=False)

    def test_get_requires_officer_login(self):
        response = self.client.get(reverse('reset_election'))
        self.assertNotEqual(response.status_code, 200)

    def test_get_shows_current_stats_including_orphaned_nota(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse('reset_election'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['student_count'], 3)
        self.assertEqual(response.context['voted_count'], 2)
        self.assertEqual(response.context['vote_count'], 1)
        # R1 contributes 9 (1 candidate vote), R2 (orphaned) contributes 10 -> 19.
        self.assertEqual(response.context['nota_votes'], 19)

    def test_post_without_confirmation_does_nothing(self):
        self.client.force_login(self.officer)
        response = self.client.post(reverse('reset_election'), {'confirm': 'not reset'})
        self.assertRedirects(response, reverse('reset_election'))

        self.voted_with_ballot.refresh_from_db()
        self.assertTrue(self.voted_with_ballot.has_voted)
        self.assertEqual(Vote.objects.filter(university=self.university).count(), 1)

    def test_post_with_confirmation_clears_votes_and_unlocks_all_students(self):
        self.client.force_login(self.officer)
        response = self.client.post(reverse('reset_election'), {'confirm': 'RESET'})
        self.assertRedirects(response, reverse('officer_portal'))

        self.assertEqual(Vote.objects.filter(university=self.university).count(), 0)
        for student in (self.voted_with_ballot, self.orphaned_voter, self.not_voted):
            student.refresh_from_db()
            self.assertFalse(student.has_voted)

        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.vote_count, 0)
        self.assertEqual(get_live_nota_votes(self.university), 0)

    def test_reset_preserves_candidates_and_student_roster(self):
        self.client.force_login(self.officer)
        self.client.post(reverse('reset_election'), {'confirm': 'RESET'})

        self.assertEqual(Candidate.objects.filter(university=self.university).count(), 1)
        self.assertEqual(Student.objects.filter(university=self.university).count(), 3)
