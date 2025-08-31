# core/views.py
from django.shortcuts import render

def voter_auth_view(request):
    """
    Renders the initial voter authentication page.
    """
    return render(request, 'voter_auth.html')