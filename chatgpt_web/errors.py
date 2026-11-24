class ClientError(Exception):
    """A public error whose message contains no request parameters or secrets."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return {"code": self.code, "message": str(self)}
