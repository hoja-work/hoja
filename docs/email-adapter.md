# SMTP email adapter

`hoja_server.email_adapter` provides a small synchronous SMTP transport
function. It does not change server routes or send mail automatically; the
application integration point and choice of provider remain deployment-specific.

## Configuration and use

Use the standard library only. Construct an `SMTPConfig` with a submission
server hostname and port. STARTTLS is enabled by default; optional credentials
must be supplied as a pair. `timeout` defaults to 10 seconds.

```python
from hoja_server.email_adapter import SMTPConfig, send_email

config = SMTPConfig(
    host="smtp.example.test",
    port=587,
    username="mailer",
    password="load-this-from-your-secret-store",
)
send_email(
    config,
    sender="notifications@example.test",
    recipients=["person@example.test"],
    subject="A notification",
    text="Your notification is ready.",
)
```

`send_email` accepts a sender, one or more recipients, a single-line subject,
and a plain-text body. It returns `None` on successful SMTP submission. This
indicates submission to the SMTP server, not final inbox delivery.

## Errors and security

Invalid connection settings raise `ValueError` during `SMTPConfig` creation.
Invalid message inputs and SMTP/network errors raise `EmailAdapterError`;
transport details are available through exception chaining, while the public
error message avoids echoing credentials or provider response content. The
adapter uses a verified TLS context for STARTTLS. Set `starttls=False` only
when the deployment intentionally uses a transport with encryption handled
outside this adapter; this module does not provide implicit-TLS mode.

Callers are responsible for obtaining credentials from a secret store, choosing
an appropriate SMTP host/port, handling retries and logging, and integrating
the adapter into application workflows. No provider-specific delivery,
templates, HTML, attachments, queueing, or automatic email behavior is included.
