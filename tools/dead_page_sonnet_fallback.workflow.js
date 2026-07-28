/**
 * Dead-page fallback OCR bằng subagent Sonnet 5 — 1 subagent / 1 trang.
 *
 * KHI DÙNG: sau khi `scan2ebook all` báo `DONE(dead=N)` — N trang bị dead-placeholder
 * (deterministic-fail, thường do provider moderation ảnh). qwen KHÔNG OCR được các trang
 * này, nhưng ảnh vẫn đọc được bằng mắt → giao subagent Sonnet đọc lại từng trang.
 *
 * BẢN CHẤT: harness này chỉ chạy TRONG phiên Claude Code (dùng Agent tool qua Workflow),
 * KHÔNG phải script CLI. Chạy: Workflow({ scriptPath: 'tools/dead_page_sonnet_fallback.workflow.js' }).
 *
 * CÁCH HOẠT ĐỘNG:
 *   1. Đọc các file work/ocr/page_*.md bắt đầu bằng DEAD_PREFIX (dead-placeholder).
 *   2. Mỗi trang dead → 1 subagent Sonnet: Read ảnh scans/page_NNN.jpg → Write ĐÈ vào
 *      work/ocr/page_NNN.md (thay placeholder bằng nội dung thật).
 *   3. Verify: đếm lại số dead-placeholder còn sót → phải giảm đúng.
 *   4. Sau đó rebuild ngoài phiên: `scan2ebook post <slug> && scan2ebook epub <slug>`
 *      (hoặc `scan2ebook all <slug> --yes --skip-prepass`).
 *
 * CẤU HÌNH: sửa BOOK_HOME + SLUGS bên dưới cho đúng batch. KHÔNG hard-code trong repo
 * đường dẫn drive (copyrighted) — điền lúc dùng, đừng commit giá trị thật.
 *
 * FAILURE MODE đã đo (POC 4 cuốn/984 trang): prompt "cấm suy luận" triệt được stub-lý-luận
 * (0 stub), NHƯNG subagent song song vẫn có xác suất ~0.2% "rớt trang câm" (không ghi file,
 * không lỗi). VÌ VẬY bước verify đếm-lại-số-file là BẮT BUỘC, không bỏ.
 */

export const meta = {
  name: 'dead-page-sonnet-fallback',
  description: 'Re-OCR các trang dead-placeholder bằng subagent Sonnet (1 subagent/trang), verify số file',
  phases: [
    { title: 'Scan', detail: 'quét dead-placeholder trong work/ocr' },
    { title: 'OCR', detail: '1 subagent Sonnet / 1 trang dead', model: 'sonnet' },
    { title: 'Verify', detail: 'đếm lại dead-placeholder còn sót' },
  ],
}

// ── CẤU HÌNH: điền lúc dùng, KHÔNG commit đường dẫn drive thật ────────────────
const BOOK_HOME = '<BOOK_HOME>'            // vd data-root chứa <slug>/work/ocr + <slug>/scans
const SLUGS = ['<slug-1>']                 // danh sách slug cần re-OCR dead pages
// ─────────────────────────────────────────────────────────────────────────────

const DEAD_PREFIX = '<!-- OCR FAILED (deterministic)'  // khớp src/scan_to_ebook/ocr.py

const RULES = `OCR RULES (sách tiếng Việt, chính tả trước 1975 có thể xuất hiện):
1. Giữ NGUYÊN dấu tiếng Việt (ả ấ ầ ẩ ẫ ậ đ...). KHÔNG bỏ dấu, KHÔNG đoán sai dấu.
2. Trung thành BẢN GỐC: chép đúng chính tả trên trang, KHÔNG hiện-đại-hoá. Tên riêng/từ nước ngoài giữ y như in.
3. Nhiều cột: đọc trái trước, phải sau. Nối liền văn bản.
4. Heading: \`## \`/\`### \`. List: \`- \`/\`1. \`. Footnote: \`[^N]\` + \`[^N]: ...\`.
5. Bỏ header/footer chạy + số trang. Hyphen cuối dòng: nối lại. Đoạn cách bằng dòng trống.

TUYỆT ĐỐI CẤM:
- KHÔNG kết luận trang "trùng/duplicate/thừa" rồi bỏ trống. Chỉ OCR đúng ảnh trước mặt.
- KHÔNG so sánh với trang khác, KHÔNG suy luận về pipeline, KHÔNG gộp/bỏ.
- Chỉ khi ảnh THỰC SỰ không có chữ nào (trang trắng vật lý) mới ghi đúng một dòng: (blank)

File .md chỉ chứa Markdown đã transcribe — KHÔNG giải thích, KHÔNG \`\`\`markdown wrapper.`

// Scan: mỗi slug tìm các trang dead trong work/ocr/page_*.md.
// Dùng subagent Scan (read-only) để đọc head file — tránh phụ thuộc fs API trong script.
phase('Scan')
const scanResults = await parallel(SLUGS.map(slug => () =>
  agent(
    `Liệt kê các trang dead-placeholder của sách "${slug}".
Thư mục OCR: ${BOOK_HOME}/${slug}/work/ocr
Với MỖI file page_*.md trong thư mục đó, đọc ~60 ký tự đầu. Nếu bắt đầu bằng
"${DEAD_PREFIX}" thì đó là trang dead cần re-OCR.
Return: CHỈ danh sách stem trang dead, mỗi dòng một stem (vd: page_042), KHÔNG gì khác.
Nếu không có trang nào, return đúng chữ: NONE`,
    { label: `scan:${slug}`, phase: 'Scan', agentType: 'general-purpose' }
  ).then(txt => ({
    slug,
    pages: (txt || '').split('\n').map(s => s.trim())
      .filter(s => /^page_\d+$/.test(s)),
  }))
))

const targets = scanResults.flatMap(r => r.pages.map(stem => ({ slug: r.slug, stem })))
log(`Dead pages tìm thấy: ${targets.length} (${scanResults.map(r => r.slug + ':' + r.pages.length).join(', ')})`)

if (targets.length === 0) {
  log('Không có trang dead nào. Xong.')
  return { dead_found: 0, reocr: 0, still_dead: 0 }
}

// OCR: 1 subagent Sonnet / 1 trang dead. Ghi ĐÈ work/ocr/page_NNN.md.
phase('OCR')
const reocr = await parallel(targets.map(t => () => {
  const img = `${BOOK_HOME}/${t.slug}/scans/${t.stem}.jpg`
  const outMd = `${BOOK_HOME}/${t.slug}/work/ocr/${t.stem}.md`
  return agent(
    `OCR engine cho sách tiếng Việt. Transcribe đúng MỘT trang scan.
Input image: ${img}
Read ảnh, transcribe, Write ĐÈ (overwrite) kết quả ra: ${outMd}

${RULES}

Return: một dòng ngắn xác nhận đã ghi (vd "wrote ${t.stem}").`,
    { label: `ocr:${t.slug}:${t.stem}`, phase: 'OCR', model: 'sonnet', agentType: 'general-purpose' }
  ).then(r => ({ slug: t.slug, stem: t.stem, ok: r != null }))
}))

const wrote = reocr.filter(r => r && r.ok)
log(`Re-OCR xong ${wrote.length}/${targets.length} trang.`)

// Verify: đếm lại dead-placeholder còn sót (BẮT BUỘC — bắt "rớt trang câm").
phase('Verify')
const verifyResults = await parallel(SLUGS.map(slug => () =>
  agent(
    `Đếm lại trang dead-placeholder còn sót của "${slug}".
Thư mục: ${BOOK_HOME}/${slug}/work/ocr
Với MỖI file page_*.md, đọc ~60 ký tự đầu; đếm số file bắt đầu bằng "${DEAD_PREFIX}".
Return: CHỈ hai dòng —
still_dead=<số file còn placeholder>
list=<stem cách nhau bởi dấu phẩy, hoặc rỗng>`,
    { label: `verify:${slug}`, phase: 'Verify', agentType: 'general-purpose' }
  ).then(txt => {
    const m = /still_dead=(\d+)/.exec(txt || '')
    return { slug, still_dead: m ? parseInt(m[1], 10) : null, raw: (txt || '').trim() }
  })
))

const stillDead = verifyResults.reduce((a, r) => a + (r.still_dead || 0), 0)
log(`Còn dead sau fallback: ${stillDead} (${verifyResults.map(r => r.slug + ':' + r.still_dead).join(', ')})`)

return {
  dead_found: targets.length,
  reocr: wrote.length,
  still_dead: stillDead,
  per_book_verify: verifyResults.map(r => ({ slug: r.slug, still_dead: r.still_dead })),
  note: stillDead > 0
    ? 'Còn trang dead — có thể "rớt câm": chạy lại workflow hoặc kiểm tay từng trang còn sót.'
    : 'Sạch. Rebuild ngoài phiên: scan2ebook all <slug> --yes --skip-prepass (hoặc post + epub).',
}
