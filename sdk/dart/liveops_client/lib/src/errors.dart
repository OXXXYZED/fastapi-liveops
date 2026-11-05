class LiveOpsException implements Exception {
  final int status;
  final String message;
  final String? responseBody;

  LiveOpsException(this.status, this.message, [this.responseBody]);

  @override
  String toString() => 'LiveOpsException($status): $message';
}

/// The request never reached the server (offline, DNS, timeout).
class NetworkException extends LiveOpsException {
  final Object? cause;
  NetworkException([this.cause]) : super(0, 'network error: server unreachable');
}

class RateLimitedException extends LiveOpsException {
  final Duration retryAfter;
  RateLimitedException(this.retryAfter, String body)
      : super(429, 'rate limited, retry after ${retryAfter.inSeconds}s', body);
}

/// Cloud save write rejected because the slot changed on the server.
class SaveConflictException extends LiveOpsException {
  /// Version currently on the server; 0 if the slot doesn't exist.
  final int currentVersion;
  SaveConflictException(this.currentVersion, String body)
      : super(409, 'save conflict, server version is $currentVersion', body);
}
