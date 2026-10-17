type SpeakerDraft = {
  name: string;
  role: string;
  identity: string;
  beliefs: string;
  personality: string;
  speakingStyle: string;
  goal: string;
  instructions: string;
};

const wordsPerMinute = 150;
const wordsPerSegment = 55;

function durationMinutes(duration: string): number {
  return Math.max(1, Number.parseInt(duration, 10) || 1);
}

function targetWordCount(duration: string): number {
  return Math.max(220, Math.round(durationMinutes(duration) * wordsPerMinute));
}

function trimText(value: string, maxLength = 240): string {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length <= maxLength ? text : `${text.slice(0, maxLength).replace(/\s+\S*$/, '')}...`;
}

function countWords(value: string): number {
  return value.trim().split(/\s+/).filter(Boolean).length;
}

function speakerLens(speaker: SpeakerDraft | undefined): 'host' | 'technical' | 'risk' | 'skeptic' | 'analyst' {
  const raw = `${speaker?.identity || speaker?.role || ''} ${speaker?.beliefs || ''} ${speaker?.personality || ''}`.toLowerCase();
  if (raw.includes('moderator') || raw.includes('host')) return 'host';
  if (raw.includes('research') || raw.includes('engineer') || raw.includes('technical')) return 'technical';
  if (raw.includes('safety') || raw.includes('risk')) return 'risk';
  if (raw.includes('skeptic') || raw.includes('challenge') || raw.includes('critic')) return 'skeptic';
  return 'analyst';
}

function personalAngle(speaker: SpeakerDraft | undefined): string {
  const angle = trimText(speaker?.instructions || speaker?.goal || '', 80).replace(/^to\s+/i, '');
  return angle ? ` My angle is ${angle.toLowerCase()}.` : '';
}

function lineFor(args: { title: string; brief: string; audience: string; speaker: SpeakerDraft | undefined; index: number }): string {
  const topic = args.title || 'this topic';
  const brief = args.brief;
  const audience = args.audience.toLowerCase();
  const beat = args.index % 6;
  const lens = speakerLens(args.speaker);
  const opening = args.index === 0 ? `Welcome. Today we are asking: ${brief}` : '';

  const host = [
    `${opening || `Let's keep the conversation grounded in ${topic}.`} We need to separate hype, adoption, and durable value so listeners know what evidence actually matters.`,
    `Before we guess timing, let's name the trigger. A market turn usually needs weaker demand, tighter budgets, and a gap between promises and results.`,
    `For ${audience}, the practical question is which projects would still be funded if teams had to defend every dollar of AI spend.`,
    `Let me pause the panel there. We have a demand story, a cost story, and a trust story; the turning point likely needs more than one of those to weaken.`,
    `For listeners, the dashboard is simple: usage, renewal rates, margins, and customer outcomes. Headlines move fast, but those measures show staying power.`,
    `Let's close the loop. Nobody can time the exact top, but we can describe the conditions that make a reset more likely.`,
  ];
  const technical = [
    `I would not treat this as one simple story. Some parts of the AI stack look overheated, while specific workflows are already producing measurable savings.${personalAngle(args.speaker)}`,
    `A concrete example is developer tooling. It can shorten search, summarization, and test writing, but teams still need review time and accountability.`,
    `The assumption I challenge is that every model improvement automatically creates revenue. Capability has to fit a workflow, a budget, and a quality bar.`,
    `For ${audience}, the risk is adopting tools without measuring cycle time, defect rates, and maintenance cost. If those do not improve, renewals get harder.`,
    `I would watch whether AI features become default infrastructure or remain premium extras. Default infrastructure is sticky; optional extras are easier to trim.`,
    `My base case is a selective reset: weaker products fade, while tools with clear savings and reliable behavior keep moving forward.`,
  ];
  const risk = [
    `The timeline depends on trust as much as revenue. A market can cool gradually, but confidence changes quickly when buyers feel the tools are hard to govern.${personalAngle(args.speaker)}`,
    `One tradeoff is speed versus assurance. Companies want automation now, yet they also need audit trails, privacy controls, and human review.`,
    `The assumption I challenge is that adoption only moves upward. If governance costs rise, leaders can slow projects even when the technology improves.`,
    `For ${audience}, evaluation is not optional. Teams that cannot prove behavior under edge cases will have a harder time keeping sponsorship.`,
    `The warning signs I would watch are longer procurement reviews, tighter approval rules, and vendors offering larger discounts to keep pilots active.`,
    `A reset would look like fewer broad claims, more proof, and a premium on systems that can be tested and explained.`,
  ];
  const skeptic = [
    `I think the correction could be larger than many people expect. When a market prices in perfect execution, merely good progress can still disappoint.${personalAngle(args.speaker)}`,
    `The example I keep coming back to is infrastructure spending. If capacity grows faster than paying usage, the gap becomes visible.`,
    `The strongest assumption is that patience lasts forever. It usually does not when revenue curves flatten and operating costs remain high.`,
    `For ${audience}, the uncomfortable point is that useful engineering work can still be repriced when business expectations were too aggressive.`,
    `I would watch consolidation, discounting, and quiet reductions in experimental budgets. Those signals often arrive before the public story changes.`,
    `My call is not that AI goes away. It is that the story shifts from unlimited expansion to selective value, and that shift can be sharp.`,
  ];
  const analyst = [
    `I would frame ${topic} as a value question first and a technology question second. The technology can be real while expectations still run too far ahead.${personalAngle(args.speaker)}`,
    `A useful benchmark is whether customers expand usage after the first pilot. Expansion suggests value; stalled pilots suggest the story was mostly enthusiasm.`,
    `The assumption I would challenge is that every company needs the same AI strategy. Many will find that smaller, focused automation is enough.`,
    `For ${audience}, durable projects need clear costs, clear ownership, and clear outcomes. The best work survives closer scrutiny.`,
    `The signs to watch are renewal rates, gross margins, and whether vendors can explain value without vague transformation language.`,
    `So my answer is conditional: the market turns when expectations outrun proof for long enough that customers and investors demand stricter evidence.`,
  ];
  return ({ host, technical, risk, skeptic, analyst }[lens])[beat];
}

export function buildConversationalPodcastSegments(title: string, brief: string, audience: string, speakers: SpeakerDraft[], duration: string) {
  const cleanTitle = trimText(title || 'Untitled episode', 120);
  const cleanBrief = trimText(brief || 'Discuss the topic with practical examples, risks, competing views, and a useful listener takeaway.', 360);
  const activeSpeakers = speakers.length ? speakers : [{ name: 'Host', role: 'Host', identity: 'Host', beliefs: '', personality: '', speakingStyle: '', goal: '', instructions: '' }];
  const targetWords = targetWordCount(duration);
  const plannedSegments = Math.min(96, Math.max(activeSpeakers.length * 2, Math.ceil(targetWords / wordsPerSegment)));
  const rows: Array<{ index: number; speaker: string; text: string }> = [];
  let words = 0;
  for (let index = 0; index < plannedSegments || words < targetWords; index += 1) {
    const speaker = activeSpeakers[index % activeSpeakers.length];
    const text = lineFor({ title: cleanTitle, brief: cleanBrief, audience, speaker, index });
    rows.push({ index: rows.length, speaker: speaker?.name || 'Host', text });
    words += countWords(text);
    if (rows.length >= 96) break;
  }
  rows.push({
    index: rows.length,
    speaker: activeSpeakers[0]?.name || 'Host',
    text: `Final thought: ${cleanTitle} will not be settled by slogans. Watch who keeps paying after pilots end, who can prove value, and who still has a business when easy assumptions are gone.`,
  });
  return rows;
}
