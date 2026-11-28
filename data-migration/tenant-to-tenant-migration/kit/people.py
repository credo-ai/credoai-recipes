"""Target users and teams for source people: the users and teams trackers first, then the target's own lists (users
by email, ignoring case; teams by exact name). Used for use case stakeholders and model owners."""

from kit.remap import AmbiguousMatch, NameIndex, casefold, exact


class People:
    """Target user and team IDs by email or team name: the users and teams trackers first, then the target's lists."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._users = self._teams = None

    def _index(self, entity, path, name_of, normalize):
        index = NameIndex(normalize=normalize)
        for record in self.ctx.tracker(entity).records:
            index.add(record["name_or_key"], record["target_id"])
        tracked = {normalize(r["name_or_key"]) for r in self.ctx.tracker(entity).records}
        for row in self.ctx.target_internal.get_all_cursor(path):
            attrs = row.get("attributes") or {}
            if attrs.get("deleted_at") or normalize(name_of(attrs)) in tracked:
                continue
            index.add(name_of(attrs), row["id"])
        return index

    def lookup(self, who):
        """(target ID, "user" or "team"), or None when the target has no match."""
        who = who or {}
        try:
            if who.get("team_name"):
                if self._teams is None:
                    self._teams = self._index("teams", "teams", lambda a: a.get("name"), exact)
                found = self._teams.lookup(who["team_name"])
                return (found, "team") if found else None
            if who.get("email"):
                if self._users is None:
                    self._users = self._index("users", "users", lambda a: a.get("email"), casefold)
                found = self._users.lookup(who["email"])
                return (found, "user") if found else None
        except AmbiguousMatch:
            return None
        return None
