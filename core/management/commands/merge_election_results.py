from collections import defaultdict

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Candidate, Student, University, Vote


class Command(BaseCommand):
    help = (
        "Merge ballots cast on another instance of this app (a separate database) "
        "into the local database for a given university. Matches candidates by "
        "department (Candidate.forum) and students by student_id, since primary "
        "keys are not shared across independently-seeded databases. Votes are "
        "merged at the individual-ballot level, never by summing vote_count "
        "counters. Students who voted on both systems are reported as conflicts "
        "and are never auto-imported or auto-resolved."
    )

    def add_arguments(self, parser):
        parser.add_argument('--remote-url', required=True, help="Base URL of the other system, e.g. https://kiosk2.local:8443")
        parser.add_argument('--university-id', required=True, dest='university_id', help="University.university_id to merge (must exist locally)")
        parser.add_argument('--token', default=None, help="Bearer token to authenticate with the remote. Defaults to settings.MERGE_API_TOKEN")
        parser.add_argument('--apply', action='store_true', default=False, help="Actually write changes. Without this flag, only a dry-run report is printed")
        parser.add_argument('--strict', action='store_true', default=False, help="Abort entirely if any remote department has no local match, instead of skipping just its votes")
        parser.add_argument('--insecure', action='store_true', default=False, help="Skip TLS certificate verification (e.g. for self-signed LAN certs)")
        parser.add_argument('--timeout', type=int, default=15, help="HTTP timeout in seconds for the export request")

    def handle(self, *args, **options):
        university_id = options['university_id']
        try:
            university = University.objects.get(university_id=university_id)
        except University.DoesNotExist:
            raise CommandError(f"No local university with university_id='{university_id}'. Aborting before contacting the remote.")

        token = options['token'] or settings.MERGE_API_TOKEN
        if not token:
            raise CommandError("No token available: pass --token or set MERGE_API_TOKEN in settings/.env.")

        payload = self._fetch_remote(options['remote_url'], university_id, token, options['timeout'], options['insecure'])

        if payload.get('university_id') != university_id:
            raise CommandError(
                f"Remote university_id mismatch: expected '{university_id}', "
                f"remote returned '{payload.get('university_id')}'. Aborting."
            )

        report = self._diff(university, payload, strict=options['strict'])

        apply_changes = options['apply']
        with transaction.atomic():
            self._write(university, report, apply_changes)
            if not apply_changes:
                transaction.set_rollback(True)

        self._print_report(university, payload, report, apply_changes)

    def _fetch_remote(self, remote_url, university_id, token, timeout, insecure):
        url = remote_url.rstrip('/') + '/officer/api/export-ballots/'
        try:
            response = requests.get(
                url,
                params={'university_id': university_id},
                headers={'Authorization': f'Bearer {token}'},
                timeout=timeout,
                verify=not insecure,
            )
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Could not reach remote system at {url}: {e}")

        if response.status_code == 403:
            raise CommandError("Remote rejected the request (403) — check the token matches MERGE_API_TOKEN on both systems.")
        if response.status_code == 404:
            raise CommandError("Remote returned 404 — check --university-id exists on the remote, and that the remote has this endpoint deployed.")
        if response.status_code != 200:
            raise CommandError(f"Remote returned unexpected status {response.status_code}: {response.text[:500]}")

        try:
            payload = response.json()
        except ValueError:
            raise CommandError("Remote response was not valid JSON.")

        for key in ('university_id', 'candidates', 'students', 'votes', 'nota_votes'):
            if key not in payload:
                raise CommandError(f"Remote response is missing expected field '{key}'.")

        return payload

    def _diff(self, university, payload, strict):
        local_candidates_by_forum = {c.forum: c for c in Candidate.objects.filter(university=university)}
        local_students_by_id = {s.student_id: s for s in Student.objects.filter(university=university)}

        remote_forums = {c['forum'] for c in payload['candidates']}
        unmatched_forums = sorted(remote_forums - set(local_candidates_by_forum.keys()))
        if unmatched_forums and strict:
            raise CommandError(f"--strict: remote has departments with no local match: {unmatched_forums}")

        remote_votes_by_student = defaultdict(list)
        for v in payload['votes']:
            student_id = v['student_id'].strip().upper()
            remote_votes_by_student[student_id].append(v)

        remote_has_voted = {s['student_id'].strip().upper(): s['has_voted'] for s in payload['students']}

        importable = {}          # student_id -> local Student
        conflicts = []           # list of dicts
        students_not_found_locally = []
        votes_skipped_unmatched_candidate = []

        for student_id, remote_voted in remote_has_voted.items():
            if not remote_voted:
                continue

            local_student = local_students_by_id.get(student_id)
            if local_student is None:
                students_not_found_locally.append(student_id)
                continue

            if local_student.has_voted:
                local_votes = list(
                    Vote.objects.filter(student=local_student)
                    .select_related('candidate')
                    .values_list('candidate__forum', 'timestamp')
                )
                remote_choices = [(v['candidate_forum'], v['timestamp']) for v in remote_votes_by_student[student_id]]
                local_forums = {f for f, _ in local_votes}
                remote_forums_for_student = {f for f, _ in remote_choices}
                conflicts.append({
                    'student_id': student_id,
                    'local_choices': local_votes,
                    'remote_choices': remote_choices,
                    'identical': local_forums == remote_forums_for_student,
                })
                continue

            staged_votes = []
            for v in remote_votes_by_student[student_id]:
                candidate = local_candidates_by_forum.get(v['candidate_forum'])
                if candidate is None:
                    votes_skipped_unmatched_candidate.append((student_id, v['candidate_forum']))
                    continue
                staged_votes.append(candidate)

            if staged_votes:
                importable[student_id] = (local_student, staged_votes)

        return {
            'unmatched_forums': unmatched_forums,
            'importable': importable,
            'conflicts': conflicts,
            'students_not_found_locally': students_not_found_locally,
            'votes_skipped_unmatched_candidate': votes_skipped_unmatched_candidate,
        }

    def _write(self, university, report, apply_changes):
        votes_created = 0
        votes_already_present = 0

        for student_id, (local_student, candidates) in report['importable'].items():
            for candidate in candidates:
                _, created = Vote.objects.get_or_create(student=local_student, candidate=candidate, defaults={'university': university})
                if created:
                    votes_created += 1
                else:
                    votes_already_present += 1

            student_locked = Student.objects.select_for_update().get(pk=local_student.pk)
            if not student_locked.has_voted:
                student_locked.has_voted = True
                student_locked.save(update_fields=['has_voted'])

        for candidate in Candidate.objects.filter(university=university):
            actual_count = Vote.objects.filter(candidate=candidate).count()
            if candidate.vote_count != actual_count:
                candidate.vote_count = actual_count
                candidate.save(update_fields=['vote_count'])

        report['votes_created'] = votes_created
        report['votes_already_present'] = votes_already_present

    def _print_report(self, university, payload, report, apply_changes):
        out = self.stdout
        style = self.style

        out.write('')
        out.write(style.SUCCESS('APPLIED') if apply_changes else style.WARNING('DRY RUN (no changes written — re-run with --apply to commit)'))
        out.write(f"University: {university.name} ({university.university_id})")
        out.write(f"Remote reported university: {payload.get('university_name')} ({payload.get('university_id')})")
        out.write('')

        if report['unmatched_forums']:
            out.write(style.WARNING(f"Unmatched departments on remote (votes skipped): {report['unmatched_forums']}"))
        else:
            out.write("All remote departments matched a local candidate.")

        out.write(f"Votes imported: {report['votes_created']} new, {report['votes_already_present']} already present (idempotent)")
        out.write(f"Students newly marked as voted: {len(report['importable'])}")
        if report['importable']:
            out.write(f"  {sorted(report['importable'].keys())}")

        if report['students_not_found_locally']:
            out.write(style.WARNING(f"Students in remote data with no local record (skipped): {sorted(report['students_not_found_locally'])}"))

        if report['votes_skipped_unmatched_candidate']:
            out.write(style.WARNING(f"Individual votes skipped (unmatched department): {report['votes_skipped_unmatched_candidate']}"))

        out.write('')
        if report['conflicts']:
            out.write(style.ERROR(f"CONFLICTS — MANUAL REVIEW REQUIRED ({len(report['conflicts'])} student(s) voted on both systems, NOT imported):"))
            for c in report['conflicts']:
                tag = 'IDENTICAL_CHOICES' if c['identical'] else 'DIFFERING_CHOICES'
                out.write(f"  [{tag}] student_id={c['student_id']}")
                out.write(f"      local:  {c['local_choices']}")
                out.write(f"      remote: {c['remote_choices']}")
        else:
            out.write("No students voted on both systems.")

        out.write('')
        out.write(
            f"NOTA — local: {university.nota_votes}, remote: {payload.get('nota_votes')} "
            "(informational only, NOT auto-merged — no per-student NOTA record exists to reconcile safely; "
            "adjust University.nota_votes manually if appropriate)."
        )

        if not apply_changes:
            out.write('')
            out.write(style.WARNING("This was a dry run. Review the report above, fix any warnings if possible, then re-run with --apply to commit."))
