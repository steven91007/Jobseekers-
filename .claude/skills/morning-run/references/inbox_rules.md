# Inbox rules — which mails count, how they are classified, what status is proposed

Used by `morning.py inbox` and `run --inbox`. Nothing here writes anything: the digest lists proposals and
the candidate changes 狀態 in the Sheet (or tells Claude to) by hand.

## Gmail query (`morning.py gmail_query`, local Google login; the connector fallback uses the same)

```
after:<start> -from:me -category:promotions -category:social
  -from:jobalerts-noreply@linkedin.com -from:newsletters-noreply@linkedin.com -from:editors-noreply@linkedin.com
  -from:security-noreply@linkedin.com -from:messages-noreply@linkedin.com -from:jobagent.stepstone.de
  -from:noreply@glassdoor.com -from:alert@indeed.com
  {from:(<ats domains below>) subject:(application OR bewerbung OR interview OR "your application" OR candidacy
   OR "next steps" OR assessment OR absage OR offer OR vorstellungsgespräch OR recruiting OR position)
   "<up to 40 open tracker companies>"}
```

- **`<start>`**: for `inbox`, `--since` or the last digest. `run` fetches from 45 days back, for the
  follow-up check and older pending proposals.
- **Calls**: per hit, `format=metadata` (From, Subject, Date, Auto-Submitted, List-Id + snippet), up to 200
  mails. `format=full` is read only when the snippet alone says ack or other. The plain-text part is used,
  else HTML with the tags stripped, at most 1500 characters.

**Job alerts and newsletters → other/newsletter, dropped and counted.** Any mail from a job board
(LinkedIn, Glassdoor, Indeed, StepStone, Arbeitnow, Otta, WTTJ, XING, Wellfound, Jobgether, Monster …)
counts as an alert unless its subject is about the candidate's own application ("your application",
"application was sent / viewed", Bewerbung, "you applied", interview). Marketing subjects also count, from
any sender: webinar, newsletter, digest, event, "% off", register now, save the date, whitepaper.

## Which mails are kept

A mail is kept when one of these holds:
1. the sender domain is an ATS / recruiting domain (list below), or
2. a company of the tracker (`求職追蹤表`, `求職追蹤表_AI 投遞`, `投遞表單`) or of the `applications` table
   matches (see "Company matching"), or
3. the subject has a recruiting word (application, Bewerbung, recruit, talent, hiring, candidate, interview,
   position, role, vacancy, Stelle, offer).

Mails classified `other` without a company match are dropped.

## Company matching

- The name is cleaned the same way as the normaliser: parentheses and GmbH, AG, SE, Inc, Ltd, LLC, B.V.,
  plc, Recycling, Group and Holding are dropped.
- **Domain match**: the sender's registrable domain (`tomra.com`, `foo.co.uk`) equals a company. This is not
  used when the sender is an ATS host.
- **Text match**: a word-boundary match of the cleaned name on the original text (display name, subject,
  snippet). This applies only to names of 5 or more characters. Shorter names ("Arm", "SAP") need the
  domain match, so "Warm regards" never maps to Arm.
- **ATS hosts are never a company.** Personio, Greenhouse, Lever, Workday, Ashby, SmartRecruiters, JOIN and
  the rest of the list below are excluded. A Personio sender maps by the company named in its subject or
  body.
- **ATS subdomains**: the company label of an ATS subdomain is a domain match.
  `sogelink.teamtailor-mail.com` gives Sogelink, `dexter-health.msg.join.com` gives dexter health.
- **First-word fallback**: a unique, distinctive first word of a multi-word name (5 or more characters, not
  a generic word like Senior, Global or Health). "EVIDENT Europe GmbH" matches the tracker row "Evident
  Microscopy".
- **Junk rows**: a name that contains an ATS host word never matches. Some old rows hold role text in the
  company cell, such as "SmartRecruiters Senior AI Engineer".
- **Several rows for one company** (dexter health, three roles): the mail goes to the row whose title it
  names. If the subject names a role none of the company's rows has ("Your application as PhD: Surface
  Physics" vs the Bosch Österreich row), there is **no proposal**, with the note "role not in tracker —
  check by hand".
- **Calendar**: it uses the same matcher, with attendee domains (gmail, outlook and similar are ignored).
- **Known limit**: Personio is both an ATS host and an employer the candidate applied to, so its own
  mails stay unmatched ("?").

## Classification (first match wins; English + German)

The digest prints the matched phrase next to each proposal, so a wrong call is visible.

| # | Kind | Signals |
|---|---|---|
| 1 | other (auto-reply) | Auto-Submitted header ≠ no; out of office, automatic reply, auto-reply, Abwesenheit(snotiz), automatische Antwort |
| 2 | offer | pleased / happy / delighted to extend / make an offer, offer letter, job offer, Vertragsangebot, Angebot unterbreiten |
| 3 | interview (invitation, in a sentence that is not conditional / future) | invite(d) you, like to invite, schedule a call / interview / time, share your availability, book / pick a slot, Calendly, reschedule, Terminvorschlag, Termin verschieben / vereinbaren, einladen, Einladung zu |
| 4 | rejection (decision) | "unfortunately … not / decided / unable to offer / other candidates whose / will not" in one sentence, regret to inform, decided not to proceed, will not be moving forward, not been successful, no longer under consideration, "leider … Absage / nicht berücksichtigen / nicht weiter / anderen Kandidaten", Absage |
| 5 | request | could you send / share / provide, please complete, assessment, take-home, coding challenge, questionnaire, salary expectation, notice period, work permit, Unterlagen nachreichen, Gehaltsvorstellung |
| 6 | ack | thank(s) you for applying / your application, received your application, Eingangsbestätigung, Bewerbung ist eingegangen |
| 7 | interview (mention) | interview, Vorstellungsgespräch, Kennenlerngespräch, phone screen, technical round |
| 8 | other | anything else |

**Why this order:**
- An invitation beats "position has been filled", because a mail can close one role and invite for another.
- A rejection needs a decision phrase. "Unfortunately the slot is taken" is scheduling, and "other
  candidates" alone appears in acks.
- A conditional or future invitation is an ack. "If the feedback is positive, we will send you a proposal
  for a first interview" (Scout24) and "Sollte das Feedback positiv ausfallen, werden wir dir ein
  Terminvorschlag … zusenden" are acks.
- A condition on the candidate's own interest keeps the invitation. "Are you still open …? If so, feel free
  to book a timeslot on my agenda" (Sogelink) is an interview.
- "We are not able to move forward" (EVIDENT) is a rejection.
- An ack beats a bare "interview" mention ("we invite selected candidates to an interview"). Plain
  "Termin" is not an invitation.
- Fixtures: `scripts/fixtures/inbox_cases.json`, checked by `morning.py selftest`.

## Proposed status (the candidate's own dropdown values)

Status means **the furthest stage reached** (memory note german-plan-notion). It is never downgraded.

| Mail | Current 狀態 | Proposal |
|---|---|---|
| any | 被拒絕 | none; "check by hand" |
| offer | anything but 收到Offer | 收到Offer |
| any other kind | 收到Offer | none |
| rejection | 準備中 / 履歷送出 | 被拒絕 + fill 結束追蹤日期 |
| rejection | 第N關面試 | none; fill 結束追蹤日期 |
| interview | 準備中 / 履歷送出 | 第一關面試 |
| interview | 第N關面試 | none; note "new round? then 第N+1關面試" |
| request | any | none; "reply needed" |
| ack | 準備中 / empty | 履歷送出 |
| ack | other | none |

A mail from a company with no tracker row is listed as "no matching application — check by hand".

## ATS / recruiting sender domains

<!-- ats-domains -->
greenhouse.io greenhouse-mail.io lever.co hire.lever.co myworkday.com myworkdayjobs.com workday.com
smartrecruiters.com ashbyhq.com personio.de personio.com recruitee.com join.com teamtailor.com teamtailormail.com
workable.com workablemail.com successfactors.com successfactors.eu sapsf.com icims.com jobvite.com bamboohr.com
softgarden.io softgarden.de rippling.com breezy.hr pinpointhq.com comeet.co jazzhr.com applytojob.com
taleo.net oraclecloud.com avature.net phenom.com eightfold.ai hibob.com dvinci.de dvinci-easy.com
umantis.com rexx-systems.com onlyfy.io prescreen.io jobs.personio.de linkedin.com indeed.com
stepstone.de arbeitnow.com otta.com welcometothejungle.com wellfound.com hired.com
<!-- /ats-domains -->

`linkedin.com` / `indeed.com` also send job alerts: those have no recruiting kind and no company match, so they
end up `other` and are dropped.
