### event_scheduler/events/views.py

from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from .models import Event, EventPermission, EventVersion, EventChangeLog, EventConflict
from .serializers import (
    EventSerializer, EventCreateSerializer, EventUpdateSerializer,
    EventPermissionSerializer, EventVersionSerializer, EventChangeLogSerializer,
    EventConflictSerializer
)
from .permissions import HasEventPermission
from .utils import detect_event_conflicts, generate_diff


class EventViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing events, including CRUD, sharing, versioning, and conflict detection.
    """
    serializer_class = EventSerializer

    def get_permissions(self):
        # Allow any authenticated user to create or batch create events
        if self.action in ['create', 'batch_create']:
            return [permissions.IsAuthenticated()]
        # For all other actions, require both authentication and event permission
        return [permissions.IsAuthenticated(), HasEventPermission()]

    def get_queryset(self):
        """
        Return events the current user has permission to access and are not deleted.
        """
        user = self.request.user
        return Event.objects.filter(
            permissions__user=user,
            is_deleted=False
        ).distinct()

    def get_serializer_class(self):
        """
        Return the appropriate serializer class based on the action.
        """
        if self.action == 'create':
            return EventCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return EventUpdateSerializer
        return EventSerializer

    def perform_create(self, serializer):
        """
        Create a new event and its initial changelog entry, then detect conflicts.
        """
        with transaction.atomic():
            # serializer.create uses context user for created_by
            event = serializer.save()
            EventChangeLog.objects.create(
                event=event,
                version=event.versions.first(),
                change_type=EventChangeLog.ChangeType.CREATE,
                changed_by=self.request.user,
                changes={'action': 'created'},
                metadata={'initial_version': True}
            )
            detect_event_conflicts(event)

    def perform_update(self, serializer):
        """
        Update an event, create a changelog entry, and detect conflicts.
        """
        with transaction.atomic():
            old_data = self.get_serializer(self.get_object()).data
            event = serializer.save()
            new_data = self.get_serializer(event).data
            EventChangeLog.objects.create(
                event=event,
                version=event.versions.latest('version_number'),
                change_type=EventChangeLog.ChangeType.UPDATE,
                changed_by=self.request.user,
                changes=generate_diff(old_data, new_data)
            )
            detect_event_conflicts(event)

    def perform_destroy(self, instance):
        """
        Soft-delete an event and create a changelog entry.
        """
        with transaction.atomic():
            instance.is_deleted = True
            instance.save()
            EventChangeLog.objects.create(
                event=instance,
                version=instance.versions.latest('version_number'),
                change_type=EventChangeLog.ChangeType.DELETE,
                changed_by=self.request.user,
                changes={'action': 'deleted'}
            )

    @action(detail=True, methods=['post'])
    def share(self, request, pk=None):
        event = self.get_object()
        serializer = EventPermissionSerializer(data=request.data, many=True, context={'event': event})
        if serializer.is_valid():
            with transaction.atomic():
                permissions = serializer.save()
                EventChangeLog.objects.create(
                    event=event,
                    version=event.versions.latest('version_number'),
                    change_type=EventChangeLog.ChangeType.SHARE,
                    changed_by=request.user,
                    changes={'permissions': serializer.data},
                    metadata={'shared_with': [p.user_id for p in permissions]}
                )
                return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def permissions(self, request, pk=None):
        event = self.get_object()
        perms = event.permissions.all()
        serializer = EventPermissionSerializer(perms, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        event = self.get_object()
        versions = event.versions.all()
        serializer = EventVersionSerializer(versions, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def changelog(self, request, pk=None):
        event = self.get_object()
        changelog = event.changelog.all()
        serializer = EventChangeLogSerializer(changelog, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def diff(self, request, pk=None):
        event = self.get_object()
        v1 = request.query_params.get('version1')
        v2 = request.query_params.get('version2')
        if not v1 or not v2:
            return Response({'error': 'version1 and version2 are required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ver1 = event.versions.get(version_number=v1)
            ver2 = event.versions.get(version_number=v2)
        except EventVersion.DoesNotExist:
            return Response({'error': 'One or both versions not found'}, status=status.HTTP_404_NOT_FOUND)
        diff = generate_diff(ver1.data, ver2.data)
        return Response(diff)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        event = self.get_object()
        version_id = request.data.get('version_id')
        if not version_id:
            return Response({'error': 'version_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            version = event.versions.get(version_number=version_id)
        except EventVersion.DoesNotExist:
            return Response({'error': 'Version not found'}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            new_v = EventVersion.objects.create(
                event=event,
                version_number=event.version + 1,
                data=version.data,
                created_by=request.user,
                change_reason=f'Rolled back to version {version_id}'
            )
            for key, val in version.data.items():
                if key not in ['id', 'created_by', 'created_at', 'updated_at', 'version']:
                    setattr(event, key, val)
            event.version += 1
            event.save()
            EventChangeLog.objects.create(
                event=event,
                version=new_v,
                change_type=EventChangeLog.ChangeType.UPDATE,
                changed_by=request.user,
                changes={'action': 'rollback', 'to_version': version_id}
            )
        return Response(EventSerializer(event).data)

    @action(detail=False, methods=['post'])
    def batch_create(self, request):
        serializer = EventCreateSerializer(data=request.data, many=True, context={'request': request})
        if serializer.is_valid():
            events = serializer.save()
            return Response(EventSerializer(events, many=True).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

