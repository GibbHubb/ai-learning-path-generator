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

export function downloadMarkdown(path) {
  const md = buildMarkdown(path);
  const slug = path.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  const blob = new Blob([md], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${slug}.md`;
  a.click();
  URL.revokeObjectURL(url);
}
