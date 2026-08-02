from django.core.management.base import BaseCommand

from core.models import Candidate, Vote


class Command(BaseCommand):
    help = (
        "Recomputes every Candidate.vote_count from the actual Vote rows. "
        "Use this to repair drift left over from votes that were deleted "
        "before the post_delete signal existed to keep the counter in sync."
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', default=False, help="Actually write changes. Without this flag, only a dry-run report is printed")

    def handle(self, *args, **options):
        apply_changes = options['apply']
        fixed = 0
        for candidate in Candidate.objects.all():
            actual_count = Vote.objects.filter(candidate=candidate).count()
            if candidate.vote_count != actual_count:
                self.stdout.write(
                    f"{candidate.name} ({candidate.forum}, {candidate.university.name}): "
                    f"{candidate.vote_count} -> {actual_count}"
                )
                if apply_changes:
                    candidate.vote_count = actual_count
                    candidate.save(update_fields=['vote_count'])
                fixed += 1

        if fixed == 0:
            self.stdout.write(self.style.SUCCESS("All candidate vote counts already match their Vote records."))
        elif apply_changes:
            self.stdout.write(self.style.SUCCESS(f"Fixed vote_count for {fixed} candidate(s)."))
        else:
            self.stdout.write(self.style.WARNING(f"{fixed} candidate(s) out of sync. Re-run with --apply to fix."))
