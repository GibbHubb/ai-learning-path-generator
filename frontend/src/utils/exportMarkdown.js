export function buildMarkdown(path) {
  const lines = [`# ${path.title}`, '', `*${path.description}*`, ''];
  lines.push(`**Level:** ${path.experience_level} | **Time:** ${path.time_commitment}`, '');

  const totalHours = (path.milestones || []).reduce((s, m) => s + m.estimated_hours, 0);
  lines.push(`**Total estimated hours:** ${totalHours}`, '');
  lines.push('---', '');

  (path.milestones || []).forEach((m, i) => {
    lines.push(`## ${i + 1}. ${m.title}`);
    lines.push('');
    lines.push(m.description);
    lines.push('');
    lines.push(`- [ ] Complete (est. ${m.estimated_hours}h)`);
    lines.push('');

    if (m.resources && m.resources.length > 0) {
      lines.push('**Resources:**');
      m.resources.forEach((r) => {
        try {
          const obj = typeof r === 'string' ? JSON.parse(r) : r;
          if (obj && obj.url) {
            lines.push(`- [${obj.title}](${obj.url}) (${obj.type})`);
          } else {
            lines.push(`- ${r}`);
          }
        } catch {
          lines.push(`- ${r}`);
        }
      });
      lines.push('');
    }
  });

  return lines.join('\n');
}

function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadMarkdown(path) {
  const md = buildMarkdown(path);
  const slug = path.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  triggerBlobDownload(new Blob([md], { type: 'text/markdown' }), `${slug}.md`);
}

// AP25 — fetch the server-built .ics and download it. apiBase is passed
// rather than imported so this module stays free of env-config concerns.
export async function downloadIcs(apiBase, pathId) {
  const res = await fetch(`${apiBase}/paths/${pathId}/calendar.ics`, { credentials: 'include' });
  if (!res.ok) {
    throw new Error(`Failed to fetch calendar: ${res.status}`);
  }
  const blob = await res.blob();
  const cd = res.headers.get('content-disposition') || '';
  const match = cd.match(/filename="?([^";]+)"?/i);
  const filename = match ? match[1] : 'learning-path.ics';
  triggerBlobDownload(blob, filename);
}
