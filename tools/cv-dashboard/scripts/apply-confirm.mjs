// Confirmation / rejection texts an application site shows after the final submit. Shared by
// scripts/apply-submit.mjs (Confirm & submit) and server/live.ts (submits made in the Live browser view).
// the site's own "not sent" message (Scout24: "Bewerbung konnte nicht gesendet werden") → submit_failed
export const REJECT_RE = /(bewerbung konnte nicht (gesendet|versendet|übermittelt) werden|konnte nicht gesendet werden|could not be (sent|submitted)|application (was )?not (sent|submitted)|submission failed|something went wrong)/i;
export const CONFIRM_RE = /(thank you|thanks for (applying|your application)|application (has been )?(received|submitted|sent)|successfully (submitted|sent|applied)|vielen dank|danke für deine bewerbung|danke für ihre bewerbung|bewerbung (wurde |ist )?(erfolgreich )?(eingegangen|gesendet|versendet|abgeschickt|übermittelt)|bedankt voor je sollicitatie)/i;
/** Message prefix of the confirm() that scripts/apply-guard.js puts in front of a final submit. */
export const GUARD_PREFIX = "Job-apply guard";
