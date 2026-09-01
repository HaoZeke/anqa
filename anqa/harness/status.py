"""Last store signal → :class:`~anqa.models.ListStatus`.

Adapters pick one store-written token and pass it here. The session
list column is :meth:`~anqa.models.SessionMeta.list_status_label`.
"""

from __future__ import annotations

from ..models import ListStatus


def from_last(token: str) -> ListStatus:
    """Map one last store signal to a :class:`~anqa.models.ListStatus`.

    Content rows and turn bookends (a user message, a bare assistant
    blob, ``task_started``, ``tool_use``) are idle. Lifecycle closes and
    store-written live flags are the other members.

    :param token: Store-specific last signal, already chosen by the adapter.
    :returns: List status member.
    """
    return ListStatus.from_token(token)
