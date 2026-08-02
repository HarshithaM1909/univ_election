# core/models.py
from django.db import models
from django.db.models import F
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
import uuid

# NEW: The central model for multi-tenancy
class University(models.Model):
    name = models.CharField(max_length=255)
    university_id = models.CharField(max_length=20, unique=True, help_text="A unique ID for the university, e.g., MSRIT_BLR")
    location = models.CharField(max_length=255, blank=True)
    nota_votes = models.PositiveIntegerField(default=0, help_text="Total NOTA votes accumulated, including unused votes from partial ballots.")

    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name_plural = "Universities"

# NEW: Extends the built-in Django User to link to a University
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    university = models.ForeignKey(University, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.user.username}'s Profile"

# UPDATED: Candidate is now linked to a University
class Candidate(models.Model):
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name='candidates')
    name = models.CharField(max_length=200)
    photo_url = models.URLField(max_length=500)
    forum = models.CharField(max_length=200)
    vote_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.name} ({self.university.name})"

    class Meta:
        ordering = ['-vote_count', 'name']
        unique_together = ('university', 'forum')

# UPDATED: Student is now linked to a University
class Student(models.Model):
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name='students')
    student_id = models.CharField(max_length=50)
    has_voted = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.student_id} ({self.university.name})"

    class Meta:
        # A student ID should be unique within a single university
        unique_together = ('university', 'student_id')

# UPDATED: Vote is also linked to a University for easier filtering
class Vote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    university = models.ForeignKey(University, on_delete=models.CASCADE, related_name='votes')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='votes_cast')
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='votes_received')
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Vote by {self.student.student_id} for {self.candidate.name}"

    class Meta:
        unique_together = ('student', 'candidate')


@receiver(post_delete, sender=Vote)
def decrement_vote_count_on_delete(sender, instance, **kwargs):
    """
    Candidate.vote_count is a denormalized counter (incremented when a Vote is
    cast) rather than a live count of Vote rows, so it must be kept in sync
    whenever a Vote is removed - including deletes from the admin, which
    bypass the voting view entirely.
    """
    Candidate.objects.filter(pk=instance.candidate_id, vote_count__gt=0).update(
        vote_count=F('vote_count') - 1
    )