"""Write-side helpers for study access control.

Every mutating helper records an :class:`accounts.models.AccessEvent` so the
access-control history is auditable, mirroring ``apikeys.views._log``. IP /
user-agent come from ``apikeys._request_meta.extract`` (already decoupled from
the apikeys app). Studio views and admin actions both call these so the audit
log and side effects stay consistent across surfaces.
"""
from __future__ import annotations

from apikeys import _request_meta

from .models import AccessEvent, AuditEvent, Invitation, Membership
from .roles import Role


def _log(experiment, event_type, *, actor=None, target_user=None, request=None, **detail):
    meta = _request_meta.extract(request) if request is not None else None
    AccessEvent.objects.create(
        experiment=experiment,
        actor=actor,
        target_user=target_user,
        event_type=event_type,
        ip_address=meta.ip_address if meta else None,
        user_agent=meta.user_agent if meta else "",
        detail=detail,
    )


def record_audit(experiment, action, *, actor=None, target="", request=None, **detail):
    """Append an :class:`AuditEvent` (edit / export / destructive action).

    Best-effort context (ip / user-agent) from the request. ``experiment`` may
    be None for cross-study actions (e.g. a data-subject request)."""
    meta = _request_meta.extract(request) if request is not None else None
    AuditEvent.objects.create(
        experiment=experiment,
        actor=actor if (actor is not None and actor.is_authenticated) else None,
        action=action,
        target=target,
        ip_address=meta.ip_address if meta else None,
        user_agent=meta.user_agent if meta else "",
        detail=detail,
    )


def grant_owner_membership(experiment, user, *, actor=None):
    """Idempotently ensure ``user`` holds the OWNER membership on experiment.

    Called when a study is created/imported so the "owner is also a Membership"
    invariant holds from the first save. Not audited — creation implies it.
    """
    if user is None:
        return None
    membership, created = Membership.objects.get_or_create(
        user=user,
        experiment=experiment,
        defaults={"role": Role.OWNER, "created_by": actor or user},
    )
    if not created and membership.role != Role.OWNER:
        membership.role = Role.OWNER
        membership.save(update_fields=["role"])
    return membership


def invite_member(experiment, email, role, *, actor, request=None):
    """Create a pending invitation (revoking any prior pending one for the
    same email so there is at most one live token per address)."""
    Invitation.objects.filter(
        experiment=experiment, email__iexact=email, accepted_at__isnull=True
    ).delete()
    invitation = Invitation.objects.create(
        experiment=experiment,
        email=email,
        role=role,
        invited_by=actor,
    )
    _log(
        experiment,
        AccessEvent.Event.INVITE_SENT,
        actor=actor,
        request=request,
        email=email,
        role=role,
    )
    return invitation


def accept_invitation(invitation, user, *, request=None):
    """Turn a pending invitation into a Membership for ``user``."""
    membership, _ = Membership.objects.get_or_create(
        user=user,
        experiment=invitation.experiment,
        defaults={"role": invitation.role, "created_by": invitation.invited_by},
    )
    invitation.mark_accepted(user)
    _log(
        invitation.experiment,
        AccessEvent.Event.INVITE_ACCEPTED,
        actor=user,
        target_user=user,
        request=request,
        email=invitation.email,
        role=invitation.role,
    )
    return membership


def revoke_invitation(invitation, *, actor, request=None):
    experiment = invitation.experiment
    email = invitation.email
    invitation.delete()
    _log(
        experiment,
        AccessEvent.Event.INVITE_REVOKED,
        actor=actor,
        request=request,
        email=email,
    )


def change_role(membership, new_role, *, actor, request=None):
    old_role = membership.role
    if old_role == new_role:
        return membership
    membership.role = new_role
    membership.save(update_fields=["role"])
    _log(
        membership.experiment,
        AccessEvent.Event.ROLE_CHANGED,
        actor=actor,
        target_user=membership.user,
        request=request,
        old_role=old_role,
        new_role=new_role,
    )
    return membership


def remove_member(membership, *, actor, request=None):
    experiment = membership.experiment
    target = membership.user
    membership.delete()
    _log(
        experiment,
        AccessEvent.Event.MEMBER_REMOVED,
        actor=actor,
        target_user=target,
        request=request,
    )


def transfer_ownership(experiment, new_owner, *, actor, request=None):
    """Make ``new_owner`` the owner; demote the previous owner to editor."""
    previous = experiment.owner
    experiment.owner = new_owner
    experiment.save(update_fields=["owner"])
    grant_owner_membership(experiment, new_owner, actor=actor)
    if previous is not None and previous != new_owner:
        Membership.objects.filter(
            experiment=experiment, user=previous
        ).update(role=Role.EDITOR)
    _log(
        experiment,
        AccessEvent.Event.OWNERSHIP_TRANSFERRED,
        actor=actor,
        target_user=new_owner,
        request=request,
        previous_owner_id=previous.pk if previous else None,
    )
