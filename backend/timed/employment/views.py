"""Viewsets for the employment app."""
import datetime

from django.contrib.auth import get_user_model
from django.db.models import CharField, DateField, IntegerField, Q, Value
from django.db.models.functions import Concat
from django.utils.translation import ugettext_lazy as _
from rest_condition import C
from rest_framework import exceptions, status
from rest_framework.decorators import detail_route
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from timed.employment import filters, models, serializers
from timed.employment.permissions import NoReports
from timed.mixins import AggregateQuerysetMixin
from timed.permissions import (IsAuthenticated, IsCreateOnly, IsDeleteOnly,
                               IsOwner, IsReadOnly, IsSuperUser, IsSupervisor,
                               IsUpdateOnly)
from timed.tracking.models import Absence, Report


class UserViewSet(ModelViewSet):
    """
    Expose user actions.

    Users are managed in admin therefore this end point
    only allows retrieving and updating.
    """

    permission_classes = [
        # only owner, superuser and supervisor may update user
        (C(IsOwner) | C(IsSuperUser) | C(IsSupervisor)) & C(IsUpdateOnly) |
        # only superuser may delete users without reports
        C(IsSuperUser) & C(IsDeleteOnly) & C(NoReports) |
        # only superuser may create users
        C(IsSuperUser) & C(IsCreateOnly) |
        # all authenticated users may read
        C(IsAuthenticated) & C(IsReadOnly)
    ]

    serializer_class = serializers.UserSerializer
    filter_class = filters.UserFilterSet
    search_fields = ('username', 'first_name', 'last_name')

    def get_queryset(self):
        return get_user_model().objects.prefetch_related(
            'employments', 'supervisees', 'supervisors'
        )

    @detail_route(methods=['post'])
    def transfer(self, request, pk=None):
        """
        Transfer worktime and absence balance to new year.

        It will skip any credits if a credit already exists on the first
        of the new year.
        """
        user = self.get_object()

        year = datetime.date.today().year
        start_year = datetime.date(year, 1, 1)
        start = datetime.date(year - 1, 1, 1)
        end = datetime.date(year - 1, 12, 31)

        # transfer absence types
        transfered_absence_credits = user.absence_credits.filter(
            date=start_year, transfer=True)
        types = models.AbsenceType.objects.filter(
            fill_worktime=False
        ).exclude(id__in=transfered_absence_credits.values('absence_type'))
        for absence_type in types:
            credit = absence_type.calculate_credit(user, start, end)
            used_days = absence_type.calculate_used_days(user, start, end)
            balance = credit - used_days
            if balance != 0:
                models.AbsenceCredit.objects.create(
                    absence_type=absence_type,
                    user=user,
                    comment=_('Transfer %(year)s') % {'year': year - 1},
                    date=start_year,
                    days=balance,
                    transfer=True
                )

        # transfer overtime
        overtime_credit = user.overtime_credits.filter(
            date=start_year, transfer=True
        )
        if not overtime_credit.exists():
            reported, expected, delta = user.calculate_worktime(start, end)
            models.OvertimeCredit.objects.create(
                user=user,
                comment=_('Transfer %(year)s') % {'year': year - 1},
                date=start_year,
                duration=delta,
                transfer=True
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class WorktimeBalanceViewSet(AggregateQuerysetMixin, ReadOnlyModelViewSet):
    """Calculate worktime for different user on different dates."""

    serializer_class = serializers.WorktimeBalanceSerializer
    filter_class = filters.WorktimeBalanceFilterSet

    def _extract_date(self):
        """
        Extract date from request.

        In detail route extract it from pk and it list
        from query params.
        """
        pk = self.request.parser_context['kwargs'].get('pk')

        # detail case
        if pk is not None:
            try:
                return datetime.datetime.strptime(
                    pk.split('_')[1], '%Y-%m-%d'
                )

            except (ValueError, TypeError, IndexError):
                raise exceptions.NotFound()

        # list case
        query_params = self.request.query_params
        try:
            return datetime.datetime.strptime(
                query_params.get('date'), '%Y-%m-%d'
            ).date()
        except ValueError:
            raise exceptions.ParseError(_('Date is invalid'))
        except TypeError:
            if query_params.get('last_reported_date', '0') == '0':
                raise exceptions.ParseError(_('Date filter needs to be set'))

            return None

    def get_queryset(self):
        date = self._extract_date()
        user = self.request.user
        queryset = get_user_model().objects.values('id')
        queryset = queryset.annotate(
            date=Value(date, DateField()),
        )
        # last_reported_date filter is set, a date can only be calucated
        # for users with either at least one absence or report
        if date is None:
            users_with_reports = Report.objects.values('user').distinct()
            users_with_absences = Absence.objects.values('user').distinct()
            active_users = users_with_reports.union(users_with_absences)
            queryset = queryset.filter(id__in=active_users)

        queryset = queryset.annotate(
            pk=Concat(
                'id',
                Value('_'),
                'date',
                output_field=CharField()
            )
        )

        if not user.is_superuser:
            queryset = queryset.filter(
                Q(id=user.id) | Q(supervisors=user)
            )

        return queryset


class AbsenceBalanceViewSet(AggregateQuerysetMixin, ReadOnlyModelViewSet):
    """Calculate absence balance for different user on different dates."""

    serializer_class = serializers.AbsenceBalanceSerializer
    filter_class = filters.AbsenceBalanceFilterSet

    def _extract_date(self):
        """
        Extract date from request.

        In detail route extract it from pk and it list
        from query params.
        """
        pk = self.request.parser_context['kwargs'].get('pk')

        # detail case
        if pk is not None:
            try:
                return datetime.datetime.strptime(
                    pk.split('_')[2], '%Y-%m-%d'
                )

            except (ValueError, TypeError, IndexError):
                raise exceptions.NotFound()

        # list case
        try:
            return datetime.datetime.strptime(
                self.request.query_params.get('date'),
                '%Y-%m-%d'
            ).date()
        except ValueError:
            raise exceptions.ParseError(_('Date is invalid'))
        except TypeError:
            raise exceptions.ParseError(_('Date filter needs to be set'))

    def _extract_user(self):
        """
        Extract user from request.

        In detail route extract it from pk and it list
        from query params.
        """
        pk = self.request.parser_context['kwargs'].get('pk')

        # detail case
        if pk is not None:
            try:
                user_id = int(pk.split('_')[0])
                # avoid query if user is self
                if self.request.user.id == user_id:
                    return self.request.user
                return get_user_model().objects.get(pk=pk.split('_')[0])
            except (ValueError, get_user_model().DoesNotExist):
                raise exceptions.NotFound()

        # list case
        try:
            user_id = self.request.query_params.get('user')
            if user_id is None:
                raise exceptions.ParseError(_('User filter needs to be set'))

            # avoid query if user is self
            if self.request.user.id == int(user_id):
                return self.request.user

            return get_user_model().objects.get(pk=user_id)
        except (ValueError, get_user_model().DoesNotExist):
            raise exceptions.ParseError(_('User is invalid'))

    def get_queryset(self):
        date = self._extract_date()
        user = self._extract_user()

        queryset = models.AbsenceType.objects.values('id')
        queryset = queryset.annotate(
            date=Value(date, DateField()),
        )
        queryset = queryset.annotate(
            user=Value(user.id, IntegerField()),
        )
        queryset = queryset.annotate(
            pk=Concat(
                'user',
                Value('_'),
                'id',
                Value('_'),
                'date',
                output_field=CharField()
            )
        )

        # only myself, superuser and supervisors may see by absence balances
        current_user = self.request.user

        if not current_user.is_superuser:
            if current_user.id != user.id:
                if not current_user.supervisees.filter(id=user.id).exists():
                    return models.AbsenceType.objects.none()

        return queryset


class EmploymentViewSet(ModelViewSet):
    serializer_class = serializers.EmploymentSerializer
    ordering = ('-end_date',)
    filter_class = filters.EmploymentFilterSet
    permission_classes = [
        # super user can add/read overtime credits
        C(IsAuthenticated) & C(IsSuperUser) |
        # user may only read filtered results
        C(IsAuthenticated) & C(IsReadOnly)
    ]

    def get_queryset(self):
        """
        Get queryset of employments.

        Following rules apply:
        1. super user may see all
        2. user may see credits of all its supervisors and self
        3. user may only see its own credit
        """
        user = self.request.user

        queryset = models.Employment.objects.select_related('user', 'location')

        if not user.is_superuser:
            queryset = queryset.filter(
                Q(user=user) | Q(user__supervisors=user)
            )

        return queryset


class LocationViewSet(ReadOnlyModelViewSet):
    """Location viewset set."""

    queryset         = models.Location.objects.all()
    serializer_class = serializers.LocationSerializer
    ordering         = ('name',)


class PublicHolidayViewSet(ReadOnlyModelViewSet):
    """Public holiday view set."""

    serializer_class = serializers.PublicHolidaySerializer
    filter_class     = filters.PublicHolidayFilterSet
    ordering         = ('date',)

    def get_queryset(self):
        """Prefetch the related data.

        :return: The public holidays
        :rtype:  QuerySet
        """
        return models.PublicHoliday.objects.select_related(
            'location'
        )


class AbsenceTypeViewSet(ReadOnlyModelViewSet):
    """Absence type view set."""

    queryset         = models.AbsenceType.objects.all()
    serializer_class = serializers.AbsenceTypeSerializer
    ordering         = ('name',)


class AbsenceCreditViewSet(ModelViewSet):
    """Absence type view set."""

    filter_class = filters.AbsenceCreditFilterSet
    serializer_class = serializers.AbsenceCreditSerializer
    permission_classes = [
        # super user can add/read absence credits
        C(IsAuthenticated) & C(IsSuperUser) |
        # user may only read filtered results
        C(IsAuthenticated) & C(IsReadOnly)
    ]

    def get_queryset(self):
        """
        Get queryset of absence credits.

        Following rules apply:
        1. super user may see all
        2. user may see credits of all its supervisors and self
        3. user may only see its own credit
        """
        user = self.request.user

        queryset = models.AbsenceCredit.objects.select_related('user')

        if not user.is_superuser:
            queryset = queryset.filter(
                Q(user=user) | Q(user__supervisors=user)
            )

        return queryset


class OvertimeCreditViewSet(ModelViewSet):
    """Absence type view set."""

    filter_class = filters.OvertimeCreditFilterSet
    serializer_class = serializers.OvertimeCreditSerializer
    permission_classes = [
        # super user can add/read overtime credits
        C(IsAuthenticated) & C(IsSuperUser) |
        # user may only read filtered results
        C(IsAuthenticated) & C(IsReadOnly)
    ]

    def get_queryset(self):
        """
        Get queryset of overtime credits.

        Following rules apply:
        1. super user may see all
        2. user may see credits of all its supervisors and self
        3. user may only see its own credit
        """
        user = self.request.user

        queryset = models.OvertimeCredit.objects.select_related('user')

        if not user.is_superuser:
            queryset = queryset.filter(
                Q(user=user) | Q(user__supervisors=user)
            )

        return queryset
