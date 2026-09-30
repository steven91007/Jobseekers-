"""Job-application tracker: read Gmail and keep a Google Sheet of applications in sync.

- ``gmail``: read-only Gmail search and message text
- ``sheets``: the user's application sheet, addressed by canonical fields
- ``google_auth``: one OAuth consent for both APIs, cached and refreshed
"""
