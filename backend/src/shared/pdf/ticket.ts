import PDFDocument from 'pdfkit';
import QRCode from 'qrcode';

export interface BoardingPassData {
  bookingId: string;
  fullName: string;
  origin: string;
  destination: string;
  departureTime: string;
  arrivalTime: string;
  seatNumber: string;
  carNumber: number;
  qrPayload: string;
}

/**
 * Genera un pase de abordar en PDF con el codigo QR incrustado.
 * Devuelve el PDF como Buffer listo para adjuntar en un email o guardar en disco.
 */
export async function generateBoardingPassPdf(data: BoardingPassData): Promise<Buffer> {
  const qrDataUrl = await QRCode.toDataURL(data.qrPayload, { margin: 1, width: 320 });
  const qrBuffer = Buffer.from(qrDataUrl.split(',')[1], 'base64');

  return new Promise<Buffer>((resolve, reject) => {
    const doc = new PDFDocument({ size: 'A5', margin: 36 });
    const chunks: Buffer[] = [];
    doc.on('data', (chunk) => chunks.push(chunk as Buffer));
    doc.on('end', () => resolve(Buffer.concat(chunks)));
    doc.on('error', reject);

    doc.rect(0, 0, doc.page.width, 70).fill('#1d4ed8');
    doc.fillColor('#ffffff').fontSize(20).text('Züge MVP', 36, 24);
    doc.fontSize(11).text('Boarding Pass', 36, 48);

    doc.fillColor('#0f172a');
    doc.moveDown(3);
    doc.fontSize(12).fillColor('#64748b').text('Fahrgast');
    doc.fontSize(14).fillColor('#0f172a').text(data.fullName);
    doc.moveDown(0.6);
    doc.fontSize(12).fillColor('#64748b').text('Strecke');
    doc.fontSize(14).fillColor('#0f172a').text(`${data.origin}  ->  ${data.destination}`);
    doc.moveDown(0.6);
    doc.fontSize(12).fillColor('#64748b').text('Abfahrt / Ankunft');
    doc.fontSize(14).fillColor('#0f172a').text(`${data.departureTime}  -  ${data.arrivalTime}`);
    doc.moveDown(0.6);
    doc.fontSize(12).fillColor('#64748b').text('Sitzplatz');
    doc.fontSize(14).fillColor('#0f172a').text(`Wagen ${data.carNumber} - Sitz ${data.seatNumber}`);

    doc.image(qrBuffer, doc.page.width - 170, doc.page.height - 190, { width: 130 });
    doc.fontSize(8).fillColor('#64748b').text(`Buchung: ${data.bookingId}`, 36, doc.page.height - 60);
    doc.fontSize(8).text(`Code: ${data.qrPayload}`, 36, doc.page.height - 48);

    doc.end();
  });
}
