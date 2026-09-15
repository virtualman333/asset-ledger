from django.urls import path

from .views import (
    ConfirmView,
    DiscardView,
    DraftListView,
    IngestImageView,
    IngestTextView,
    JobDetailView,
)

app_name = "ingest"

urlpatterns = [
    path("image/", IngestImageView.as_view(), name="ingest-image"),
    path("text/", IngestTextView.as_view(), name="ingest-text"),
    path("jobs/<int:pk>/", JobDetailView.as_view(), name="job-detail"),
    path("drafts/", DraftListView.as_view(), name="drafts"),
    path("drafts/<int:pk>/confirm/", ConfirmView.as_view(), name="draft-confirm"),
    path("drafts/<int:pk>/discard/", DiscardView.as_view(), name="draft-discard"),
]
