# Security Policy

## Supported versions

| Version | Supported |
| :--- | :--- |
| 0.1.x | yes |

## Reporting a vulnerability

Email **supritu4@gmail.com** with a description, the affected version and a
reproduction if you have one. Please do not open a public issue for a
vulnerability before it is fixed.

You can expect an acknowledgement within 72 hours.

## Scope notes

PulseWatch fetches URLs you configure and posts to webhooks you configure.
Treat `targets.json` and `.env` as secrets: they routinely contain tokens for
Telegram, Slack-style webhooks or authenticated endpoints.
