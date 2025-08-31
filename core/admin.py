from django.contrib import admin
from .models import Candidate, Student, Vote

# Register your models here.

@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ('name', 'forum', 'vote_count')
    search_fields = ('name', 'forum')

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'has_voted')
    search_fields = ('student_id',)
    list_filter = ('has_voted',)

@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ('student', 'candidate', 'timestamp')
    list_filter = ('timestamp', 'candidate')
    autocomplete_fields = ['student', 'candidate']
