# core/views.py
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse
from django.conf import settings
from django.template.loader import render_to_string
from .models import Student, Candidate, Vote, University, Profile
from .blockchain_utils import send_vote_to_blockchain
from .forms import CandidateForm
from django.core.paginator import Paginator
import datetime
import csv
import io

# =============================================================================
# Helper Functions
# =============================================================================
def is_superuser(user):
    return user.is_superuser

def get_current_university(request):
    """
    Returns the University instance relevant to the current request.
    
    For logged-in officers: uses their Profile's linked university.
    For kiosk/anonymous flows: falls back to the first University in the database.
    
    Returns None if no university exists at all.
    """
    if request.user.is_authenticated:
        try:
            profile = Profile.objects.select_related('university').get(user=request.user)
            return profile.university
        except Profile.DoesNotExist:
            pass
    
    # Fallback: use the first (or only) university in the database
    return University.objects.first()

# =============================================================================
# Main Homepage
# =============================================================================
def homepage_view(request):
    return render(request, 'homepage.html')

# =============================================================================
# Voter Flow Views
# =============================================================================
def voter_auth_view(request):
    university = get_current_university(request)
    
    if request.method == 'POST':
        student_id_from_form = request.POST.get('student_id', '').strip().upper()
        if not student_id_from_form:
            messages.error(request, 'Please provide a Student ID.')
            return redirect('voter_kiosk')

        if not university:
            messages.error(request, 'No university is configured. Please contact the election officer.')
            return redirect('voter_kiosk')

        try:
            student = Student.objects.get(university=university, student_id=student_id_from_form)
            if student.has_voted:
                messages.warning(request, f"The Student ID '{student.student_id}' has already been used to vote.")
                return redirect('voter_kiosk')
            
            request.session['verified_student_pk'] = student.pk
            return redirect('voting_page')
        except Student.DoesNotExist:
            messages.error(request, f"Student ID '{student_id_from_form}' not found. Please check the ID and try again.")
            return redirect('voter_kiosk')

    return render(request, 'voter_auth.html')

def voting_view(request):
    student_pk = request.session.get('verified_student_pk')
    if not student_pk:
        messages.error(request, "Authentication required. Please verify your Student ID.")
        return redirect('voter_kiosk')

    try:
        student = Student.objects.select_related('university').get(pk=student_pk)
    except Student.DoesNotExist:
        messages.error(request, "Invalid session. Please re-authenticate.")
        if 'verified_student_pk' in request.session:
            del request.session['verified_student_pk']
        return redirect('voter_kiosk')

    if student.has_voted:
        messages.warning(request, "You have already cast your vote.")
        if 'verified_student_pk' in request.session:
            del request.session['verified_student_pk']
        return redirect('voter_kiosk')

    university = student.university

    if request.method == 'POST':
        selected_pks = request.POST.getlist('candidate_pks')
        if len(selected_pks) != 10:
            messages.error(request, "You must select exactly 10 candidates.")
            candidates = Candidate.objects.filter(university=university).order_by('name')
            return render(request, 'voting_page.html', {'candidates': candidates})

        try:
            with transaction.atomic():
                student_to_update = Student.objects.select_for_update().get(pk=student.pk)
                if student_to_update.has_voted:
                    messages.warning(request, "Vote has already been cast.")
                    return redirect('voter_kiosk')

                for pk in selected_pks:
                    if pk != 'nota':
                        candidate = Candidate.objects.get(pk=pk, university=university)
                        candidate.vote_count = F('vote_count') + 1
                        candidate.save(update_fields=['vote_count'])
                        Vote.objects.create(
                            university=university,
                            student=student,
                            candidate=candidate,
                        )
                
                student_to_update.has_voted = True
                student_to_update.save(update_fields=['has_voted'])

            print(f"Submitting vote for student {student.student_id} to the blockchain...")
            try:
                tx_hash = send_vote_to_blockchain(student.student_id)
                if tx_hash:
                    print(f"Vote successfully recorded on the blockchain! Tx Hash: {tx_hash}")
                else:
                    print(f"WARNING: Vote for {student.student_id} saved locally, but failed to record on the blockchain.")
            except Exception as e:
                print(f"WARNING: Vote for {student.student_id} saved locally, but a blockchain error occurred: {e}")

        except Exception as e:
            messages.error(request, f"An error occurred while submitting your vote. Please try again. Error: {e}")
            return redirect('voting_page')

        del request.session['verified_student_pk']
        return redirect('thank_you')

    candidates = Candidate.objects.filter(university=university).order_by('name')
    context = {'candidates': candidates}
    return render(request, 'voting_page.html', context)

def thank_you_view(request):
    return render(request, 'thank_you.html')

# =============================================================================
# Officer Flow Views
# =============================================================================
def officer_login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user.is_superuser:
                login(request, user)
                return redirect('officer_portal')
            else:
                messages.error(request, "Access Denied. This portal is for authorized officers only.")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
    return render(request, 'officer_login.html', {'form': form})

@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def officer_portal_view(request):
    return render(request, 'officer_portal.html')

@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def dashboard_view(request):
    university = get_current_university(request)
    candidates = Candidate.objects.filter(university=university).order_by('-vote_count')
    total_votes = Vote.objects.filter(university=university).count()
    context = {'candidates': candidates, 'total_votes_cast': total_votes}
    return render(request, 'dashboard.html', context)

@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def dashboard_api_view(request):
    university = get_current_university(request)
    candidates_data = Candidate.objects.filter(university=university).order_by('-vote_count').values(
        'name', 'photo_url', 'forum', 'vote_count'
    )
    total_votes = Vote.objects.filter(university=university).count()
    data = {'candidates': list(candidates_data), 'total_votes_cast': total_votes}
    return JsonResponse(data)

@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def import_students_view(request):
    university = get_current_university(request)
    
    if request.method == 'POST':
        if not university:
            messages.error(request, "No university is configured. Please create one in the admin panel first.")
            return redirect('import_students')
        
        csv_file = request.FILES.get('student_roster_file')
        if not csv_file or not csv_file.name.endswith('.csv'):
            messages.error(request, "Please upload a valid CSV file.")
            return redirect('import_students')
        
        try:
            decoded_file = csv_file.read().decode('utf-8')
            io_string = io.StringIO(decoded_file)
            reader = csv.reader(io_string)
            next(reader)  # Skip header
            
            students_to_create = [
                Student(university=university, student_id=row[0].strip().upper())
                for row in reader if row and row[0].strip()
            ]

            before_count = Student.objects.filter(university=university).count()
            Student.objects.bulk_create(students_to_create, ignore_conflicts=True)
            after_count = Student.objects.filter(university=university).count()
            messages.success(request, f"Successfully imported {after_count - before_count} new students ({len(students_to_create)} rows read from file).")
        except Exception as e:
            messages.error(request, f"An error occurred during import: {e}")
        return redirect('officer_portal')
    return render(request, 'import_students.html')

# =============================================================================
# Results Flow Views
# =============================================================================
@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def results_reveal_view(request, token):
    valid_token = settings.RESULTS_ACCESS_TOKEN
    if not valid_token or token != valid_token:
        return HttpResponseForbidden("Access Denied: Invalid security token.")

    university = get_current_university(request)
    winners = Candidate.objects.filter(university=university).order_by('-vote_count')[:10]
    positions = [
        "President", "Vice President", "General Secretary", "Treasurer",
        "Cultural Secretary", "Sports Secretary", "Technical Head",
        "Welfare Officer", "Publications Head", "Marketing Head"
    ]
    results = list(zip(winners, positions))
    context = {'results': results}
    return render(request, 'results_reveal.html', context)

@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def go_to_results_view(request):
    token = settings.RESULTS_ACCESS_TOKEN
    if not token:
        messages.error(request, "Results Access Token is not configured in the settings.")
        return redirect('officer_portal')
    return redirect(reverse('results_reveal', kwargs={'token': token}))

@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def download_results_pdf(request):
    university = get_current_university(request)
    winners = Candidate.objects.filter(university=university).order_by('-vote_count')[:10]
    positions = [
        "President", "Vice President", "General Secretary", "Treasurer",
        "Cultural Secretary", "Sports Secretary", "Technical Head",
        "Welfare Officer", "Publications Head", "Marketing Head"
    ]
    results = list(zip(winners, positions))
    context = {'results': results, 'date': datetime.date.today()}
    
    html_string = render_to_string('results_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="election-results-{context["date"]}.pdf"'
    
    # Lazy import to avoid crashing the app if WeasyPrint's native libs aren't installed
    from weasyprint import HTML
    HTML(string=html_string).write_pdf(response)
    return response

# NEW view for the Roster Builder page
@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def create_roster_view(request):
    return render(request, 'create_roster.html')

# NEW view for displaying the voter roster
@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def voter_list_view(request):
    university = get_current_university(request)
    student_list = Student.objects.filter(university=university).order_by('student_id')
    
    # Paginate the list to handle thousands of students
    paginator = Paginator(student_list, 50) # Show 50 students per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'total_students': student_list.count()
    }
    return render(request, 'voter_list.html', context)

# NEW view for listing and managing candidates
@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def manage_candidates_view(request):
    university = get_current_university(request)
    candidates = Candidate.objects.filter(university=university).order_by('name')
    form = CandidateForm()
    context = {
        'candidates': candidates,
        'form': form
    }
    return render(request, 'manage_candidates.html', context)

# NEW view for adding a candidate (handles the form submission)
@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def add_candidate_view(request):
    if request.method == 'POST':
        university = get_current_university(request)
        if not university:
            messages.error(request, "No university is configured. Please create one in the admin panel first.")
            return redirect('manage_candidates')
        form = CandidateForm(request.POST)
        if form.is_valid():
            candidate = form.save(commit=False)
            candidate.university = university
            candidate.save()
            messages.success(request, f"Candidate '{form.cleaned_data['name']}' added successfully.")
        else:
            messages.error(request, "There was an error with your submission.")
    return redirect('manage_candidates')

# NEW view for deleting a candidate
@login_required(login_url='officer_login')
@user_passes_test(is_superuser, login_url='officer_login')
def delete_candidate_view(request, pk):
    if request.method == 'POST':
        university = get_current_university(request)
        try:
            candidate = Candidate.objects.get(pk=pk, university=university)
            messages.warning(request, f"Candidate '{candidate.name}' has been deleted.")
            candidate.delete()
        except Candidate.DoesNotExist:
            messages.error(request, "Candidate not found.")
    return redirect('manage_candidates')
