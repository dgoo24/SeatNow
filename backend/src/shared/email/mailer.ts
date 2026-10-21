import nodemailer, { type Transporter } from 'nodemailer';
import { env, isMailEnabled } from '../../config/env';

let transporter: Transporter | null = null;

function getTransporter(): Transporter | null {
  if (!isMailEnabled) return null;
  if (transporter) return transporter;
  transporter = nodemailer.createTransport({
    host: env.SMTP_HOST,
    port: env.SMTP_PORT,
    secure: env.SMTP_SECURE,
    auth: { user: env.SMTP_USER, pass: env.SMTP_PASS },
  });
  return transporter;
}

export interface MailAttachment {
  filename: string;
  content: Buffer;
  contentType?: string;
}

export interface SendMailInput {
  to: string;
  subject: string;
  html: string;
  attachments?: MailAttachment[];
}

export async function sendMail(input: SendMailInput): Promise<boolean> {
  const tx = getTransporter();
  if (!tx) {
    // eslint-disable-next-line no-console
    console.warn(`[mail] SMTP no configurado. Email a ${input.to} no enviado (subject: "${input.subject}").`);
    return false;
  }
  try {
    await tx.sendMail({
      from: env.MAIL_FROM,
      to: input.to,
      subject: input.subject,
      html: input.html,
      attachments: input.attachments,
    });
    return true;
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[mail] Error enviando email:', (err as Error).message);
    return false;
  }
}

function layout(title: string, body: string): string {
  return `<!doctype html>
<html lang="de">
  <body style="margin:0;background:#0f172a;font-family:Segoe UI,Arial,sans-serif;color:#0f172a;">
    <div style="max-width:560px;margin:24px auto;background:#ffffff;border-radius:12px;overflow:hidden;">
      <div style="background:#1d4ed8;padding:20px 24px;color:#fff;font-size:18px;font-weight:600;">
        ${title}
      </div>
      <div style="padding:24px;font-size:15px;line-height:1.55;color:#1e293b;">
        ${body}
      </div>
      <div style="padding:16px 24px;background:#f1f5f9;color:#64748b;font-size:12px;">
        Züge MVP - Buchungsplattform für Bahntickets.
      </div>
    </div>
  </body>
</html>`;
}

export interface BookingEmailData {
  fullName: string;
  origin: string;
  destination: string;
  departureTime: string;
  seatNumber: string;
  carNumber: number;
  qrPayload?: string | null;
}

export function bookingConfirmedHtml(data: BookingEmailData): string {
  return layout(
    'Dein Ticket ist bestätigt',
    `<p>Hallo <strong>${data.fullName}</strong>,</p>
     <p>Deine Buchung wurde bestätigt. Im Anhang findest du deinen Boarding Pass als PDF.</p>
     <table style="width:100%;border-collapse:collapse;margin:16px 0;">
       <tr><td style="padding:6px 0;color:#64748b;">Strecke</td><td style="text-align:right;font-weight:600;">${data.origin} -> ${data.destination}</td></tr>
       <tr><td style="padding:6px 0;color:#64748b;">Abfahrt</td><td style="text-align:right;font-weight:600;">${data.departureTime}</td></tr>
       <tr><td style="padding:6px 0;color:#64748b;">Sitzplatz</td><td style="text-align:right;font-weight:600;">Wagen ${data.carNumber} - ${data.seatNumber}</td></tr>
       ${data.qrPayload ? `<tr><td style="padding:6px 0;color:#64748b;">Code</td><td style="text-align:right;font-weight:600;">${data.qrPayload}</td></tr>` : ''}
     </table>`,
  );
}

export function bookingFailedHtml(data: BookingEmailData, reason: string): string {
  return layout(
    'Deine Buchung konnte nicht bestätigt werden',
    `<p>Hallo <strong>${data.fullName}</strong>,</p>
     <p>Dein Sitzplatz für die Strecke <strong>${data.origin} -> ${data.destination}</strong> konnte nicht bestätigt werden.</p>
     <p>Grund: ${reason}</p>
     <p>Die Reservierung deiner Karte wurde aufgehoben. Es erfolgte keine Abbuchung.</p>`,
  );
}

export interface TripDisruptionEmailData extends BookingEmailData {
  oldDepartureTime: string;
  newDepartureTime?: string | null;
  newOrigin?: string | null;
  newDestination?: string | null;
  newSeatNumber?: string | null;
  newCarNumber?: number | null;
  bookingId: string;
}

/** Zugausfall mit Ersatzverbindung: Sitzplatz automatisch umgebucht. */
export function bookingReassignedHtml(d: TripDisruptionEmailData): string {
  return layout(
    'Dein Zug wurde umgebucht',
    `<p>Hallo <strong>${d.fullName}</strong>,</p>
     <p>Dein Zug <strong>${d.origin} -> ${d.destination}</strong> (Abfahrt ${d.departureTime}) ist ausgefallen.</p>
     <p>Wir haben dich automatisch auf die nächste Verbindung umgebucht:</p>
     <table style="width:100%;border-collapse:collapse;margin:16px 0;">
       <tr><td style="padding:6px 0;color:#64748b;">Neue Verbindung</td><td style="text-align:right;font-weight:600;">${d.newOrigin} -> ${d.newDestination}</td></tr>
       <tr><td style="padding:6px 0;color:#64748b;">Abfahrt</td><td style="text-align:right;font-weight:600;">${d.newDepartureTime ?? '-'}</td></tr>
       <tr><td style="padding:6px 0;color:#64748b;">Neuer Sitzplatz</td><td style="text-align:right;font-weight:600;">${d.newCarNumber != null ? `Wagen ${d.newCarNumber} - ` : ''}${d.newSeatNumber ?? '-'}</td></tr>
       <tr><td style="padding:6px 0;color:#64748b;">Buchung</td><td style="text-align:right;font-weight:600;">${d.bookingId.slice(0, 8)}</td></tr>
     </table>
     <p>Ein neuer Boarding Pass folgt per E-Mail. Entschuldige die Störung.</p>`,
  );
}

/** Zugausfall ohne Ersatz: Rueckerstattung/Kompensation ausstehend. */
export function bookingCancelledHtml(d: TripDisruptionEmailData, options: string[]): string {
  return layout(
    'Dein Zug ist ausgefallen – deine Optionen',
    `<p>Hallo <strong>${d.fullName}</strong>,</p>
     <p>Dein Zug <strong>${d.origin} -> ${d.destination}</strong> (Abfahrt ${d.departureTime}) ist leider ausgefallen.
        Eine automatische Umbuchung war leider nicht möglich.</p>
     <p>Deine Buchung <strong>${d.bookingId.slice(0, 8)}</strong> befindet sich im Status
        <em>Rückerstattung ausstehend</em>. Bitte wähle eine Option:</p>
     <ul style="padding-left:18px;">
       ${options.map((o) => `<li>${o}</li>`).join('\n       ')}
     </ul>
     <p>Der betroffene Sitzplatz wurde freigegeben. Es erfolgt keine weitere Abbuchung ohne deine Bestätigung.</p>`,
  );
}

/** Kritische Verspaetung: proaktive Benachrichtigung (keine Statusaenderung). */
export function criticalDelayHtml(d: TripDisruptionEmailData, delayMinutes: number): string {
  return layout(
    `Dein Zug hat ${delayMinutes} Minuten Verspätung`,
    `<p>Hallo <strong>${d.fullName}</strong>,</p>
     <p>Dein Zug <strong>${d.origin} -> ${d.destination}</strong> (Abfahrt ${d.departureTime}) hat aktuell
        <strong>${delayMinutes} Minuten Verspätung</strong>.</p>
     <p>Deine Reservierung bleibt bestehen. Prüfe kurz vor Abfahrt die Live-Anzeige in der App.</p>`,
  );
}
