from django.db import models
import uuid

# Create your models here.

class Candidate(models.Model):
    """Represents a candidate in the election."""
    name = models.CharField(max_length=200, help_text="Full name of the candidate.")
    photo_url = models.URLField(max_length=500, help_text="A direct URL to the candidate's formal picture.")
    forum = models.CharField(max_length=200, help_text="The forum or party the candidate represents.")
    vote_count = models.PositiveIntegerField(default=0, help_text="The current number of votes received.")

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-vote_count', 'name'] # Default order for queries


class Student(models.Model):
    """Represents a student voter."""
    student_id = models.CharField(max_length=50, unique=True, help_text="The unique student ID number.")
    has_voted = models.BooleanField(default=False, help_text="True if the student has already cast their votes.")
    
    # We can add more fields here later, like name, email, etc. if needed.
    
    def __str__(self):
        return self.student_id

class Vote(models.Model):
    """Represents a single vote cast by a student for a candidate."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='votes')
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='votes')
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Vote by {self.student.student_id} for {self.candidate.name}"

    class Meta:
        # Ensures a student can't vote for the same candidate twice
        unique_together = ('student', 'candidate')