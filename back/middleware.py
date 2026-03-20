import re


class NormalizePathSlashesMiddleware:
    """Normalize repeated slashes in request paths before URL resolution."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path_info = request.META.get("PATH_INFO", "")
        if path_info:
            normalized = re.sub(r"/{2,}", "/", path_info)
            if not normalized.startswith("/"):
                normalized = f"/{normalized}"
            request.META["PATH_INFO"] = normalized
            request.path_info = normalized

        return self.get_response(request)