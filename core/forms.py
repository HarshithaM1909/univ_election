# core/forms.py
from django import forms
from .models import Candidate

class CandidateForm(forms.ModelForm):
    class Meta:
        model = Candidate
        fields = ['name', 'photo_url', 'forum']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full px-4 py-2 bg-black-600 rounded-lg focus:ring-orange_wheel'}),
            'photo_url': forms.URLInput(attrs={'class': 'w-full px-4 py-2 bg-black-600 rounded-lg focus:ring-orange_wheel'}),
            'forum': forms.TextInput(attrs={'class': 'w-full px-4 py-2 bg-black-600 rounded-lg focus:ring-orange_wheel'}),
        }
        labels = {
            'forum': 'Department',
        }