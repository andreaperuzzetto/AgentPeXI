#!/usr/bin/env node
/**
 * Puppeteer render bridge — invocato da Python via subprocess.
 *
 * Input (stdin): JSON con i seguenti campi:
 *   {
 *     "html":        string,          // HTML da renderizzare (stringa, non path)
 *     "output_path": string,          // path dove scrivere l'output
 *     "format":      "png" | "pdf",   // formato output (default: "png")
 *     "viewport": {                   // opzionale
 *       "width":            number,   // default: 1440
 *       "height":           number,   // default: 900
 *       "deviceScaleFactor": number   // default: 2.0
 *     },
 *     "pdf_options": {                // solo se format = "pdf"
 *       "format": "A4" | "A3",       // default: "A4"
 *       "printBackground": boolean,  // default: true
 *       "margin": {                  // default: tutti 0
 *         "top": string,
 *         "right": string,
 *         "bottom": string,
 *         "left": string
 *       }
 *     }
 *   }
 *
 * Output: esce 0 in caso di successo, 1 in caso di errore (messaggio su stderr).
 *
 * Usage da Python:
 *   import subprocess, json
 *   result = subprocess.run(
 *       ["node", "scripts/render.js"],
 *       input=json.dumps(args),
 *       capture_output=True, text=True, timeout=60
 *   )
 *   if result.returncode != 0:
 *       raise RenderError(result.stderr)
 */

import puppeteer from "puppeteer";
import fs from "fs/promises";
import path from "path";

async function main() {
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  let args;
  try {
    args = JSON.parse(input);
  } catch (e) {
    process.stderr.write(`render.js: invalid JSON input: ${e.message}\n`);
    process.exit(1);
  }

  const {
    html,
    output_path,
    format = "png",
    viewport = {},
    pdf_options = {},
  } = args;

  if (!html) {
    process.stderr.write("render.js: missing required field: html\n");
    process.exit(1);
  }
  if (!output_path) {
    process.stderr.write("render.js: missing required field: output_path\n");
    process.exit(1);
  }
  if (!["png", "pdf"].includes(format)) {
    process.stderr.write(`render.js: invalid format: ${format} (must be png or pdf)\n`);
    process.exit(1);
  }

  const viewportConfig = {
    width: viewport.width ?? 1440,
    height: viewport.height ?? 900,
    deviceScaleFactor: viewport.deviceScaleFactor ?? 2.0,
  };

  // Crea directory di output se non esiste
  await fs.mkdir(path.dirname(output_path), { recursive: true });

  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport(viewportConfig);

    // Carica HTML come stringa (non come URL)
    await page.setContent(html, { waitUntil: "networkidle0" });

    if (format === "png") {
      await page.screenshot({
        path: output_path,
        fullPage: true,
        type: "png",
      });
    } else {
      // format === "pdf"
      const pdfConfig = {
        path: output_path,
        format: pdf_options.format ?? "A4",
        printBackground: pdf_options.printBackground ?? true,
        margin: pdf_options.margin ?? {
          top: "0",
          right: "0",
          bottom: "0",
          left: "0",
        },
      };
      await page.pdf(pdfConfig);
    }
  } finally {
    await browser.close();
  }

  process.exit(0);
}

main().catch((err) => {
  process.stderr.write(`render.js: unexpected error: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
