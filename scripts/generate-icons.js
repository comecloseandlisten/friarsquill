const sharp = require('sharp');
const fs = require('fs');
const path = require('path');

const svgPath = path.join(__dirname, '..', 'electron', 'renderer', 'icon.svg');
const outDir = path.join(__dirname, '..', 'electron', 'renderer');

function buildIco(pngBuffers) {
  const count = pngBuffers.length;
  const headerSize = 6;
  const dirEntrySize = 16;
  const dirSize = dirEntrySize * count;
  let dataOffset = headerSize + dirSize;

  // ICO header: reserved(2) + type=1(2) + count(2)
  const header = Buffer.alloc(headerSize);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(count, 4);

  const dirEntries = [];
  const dataChunks = [];

  for (const { size, buf } of pngBuffers) {
    const entry = Buffer.alloc(dirEntrySize);
    entry.writeUInt8(size >= 256 ? 0 : size, 0);  // width (0 = 256)
    entry.writeUInt8(size >= 256 ? 0 : size, 1);  // height
    entry.writeUInt8(0, 2);   // color palette
    entry.writeUInt8(0, 3);   // reserved
    entry.writeUInt16LE(1, 4);  // color planes
    entry.writeUInt16LE(32, 6); // bits per pixel
    entry.writeUInt32LE(buf.length, 8);  // data size
    entry.writeUInt32LE(dataOffset, 12); // data offset
    dirEntries.push(entry);
    dataChunks.push(buf);
    dataOffset += buf.length;
  }

  return Buffer.concat([header, ...dirEntries, ...dataChunks]);
}

async function main() {
  const svgBuf = fs.readFileSync(svgPath);
  const sizes = [16, 32, 48, 64, 128, 256];
  const pngBuffers = [];

  for (const size of sizes) {
    const buf = await sharp(svgBuf, { density: Math.round(72 * size / 48) })
      .resize(size, size)
      .png()
      .toBuffer();
    pngBuffers.push({ size, buf });
    console.log(`  ${size}x${size} PNG`);
  }

  // icon.png (256px for macOS/Linux)
  const png256 = pngBuffers.find(p => p.size === 256);
  fs.writeFileSync(path.join(outDir, 'icon.png'), png256.buf);
  console.log('icon.png saved');

  // icon.ico (Windows)
  const ico = buildIco(pngBuffers);
  fs.writeFileSync(path.join(outDir, 'icon.ico'), ico);
  console.log('icon.ico saved');
}

main().catch(err => { console.error(err); process.exit(1); });
