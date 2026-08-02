# core/urls.py
from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # == Main Entry Point ==
    path('', views.homepage_view, name='homepage'),

    # == Voter Flow ==
    # The voter kiosk is now at its own dedicated URL
    path('kiosk/', views.voter_auth_view, name='voter_kiosk'), 
    path('vote/', views.voting_view, name='voting_page'),
    path('thank-you/', views.thank_you_view, name='thank_you'),

    # == Officer Portal & Actions ==
    path('officer/login/', views.officer_login_view, name='officer_login'),
    path('officer/portal/', views.officer_portal_view, name='officer_portal'),
    path('officer/manage-candidates/', views.manage_candidates_view, name='manage_candidates'), # <-- ADD
    path('officer/add-candidate/', views.add_candidate_view, name='add_candidate'),          # <-- ADD
    path('officer/delete-candidate/<int:pk>/', views.delete_candidate_view, name='delete_candidate'), # <-- ADD
    # ...
    path('officer/voter-list/', views.voter_list_view, name='voter_list'),
    path('officer/create-roster/', views.create_roster_view, name='create_roster'),
    path('officer/api/export-ballots/', views.export_ballots_view, name='export_ballots_api'),
    path('officer/logout/', auth_views.LogoutView.as_view(next_page='homepage'), name='officer_logout'),

    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('dashboard/api/', views.dashboard_api_view, name='dashboard_api'),
    path('dashboard/import-students/', views.import_students_view, name='import_students'),
    
    path('results/reveal/<str:token>/', views.results_reveal_view, name='results_reveal'),
    path('results/download/', views.download_results_pdf, name='download_results_pdf'),
    path('go-to-results/', views.go_to_results_view, name='go_to_results'),
]