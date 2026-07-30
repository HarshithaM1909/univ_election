from django.contrib import admin
from .models import University, Profile, Candidate, Student, Vote

# Register your models here.
@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ('name', 'university_id', 'location')
    search_fields = ('name', 'university_id')

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'university')

@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ('name', 'forum', 'vote_count', 'university')
    search_fields = ('name', 'forum')
    list_filter = ('university',)

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'has_voted', 'university')
    search_fields = ('student_id',)
    list_filter = ('has_voted', 'university')

@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ('student', 'candidate', 'timestamp')
    list_filter = ('timestamp', 'candidate__university')
    autocomplete_fields = ['student', 'candidate']
