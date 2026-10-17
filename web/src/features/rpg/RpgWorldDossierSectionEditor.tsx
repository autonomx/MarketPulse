import type {
  RpgAuthoringDossierQuickFact,
  RpgAuthoringDossierSection,
  RpgAuthoringEntityDossier,
} from './api/rpgWorldAuthoringClient';
import './RpgWorldDossierSectionEditor.css';

interface RpgWorldDossierSectionEditorProps {
  dossier: RpgAuthoringEntityDossier;
  entityTitle: string;
  onChange: (dossier: RpgAuthoringEntityDossier) => void;
}

function slug(value: string, fallback: string): string {
  const normalized = value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return normalized || fallback;
}

export function RpgWorldDossierSectionEditor({
  dossier,
  entityTitle,
  onChange,
}: RpgWorldDossierSectionEditorProps) {
  const setSections = (sections: RpgAuthoringDossierSection[]) => onChange({ ...dossier, sections });
  const updateSection = (index: number, changes: Partial<RpgAuthoringDossierSection>) => {
    setSections(dossier.sections.map((section, sectionIndex) => (
      sectionIndex === index ? { ...section, ...changes } : section
    )));
  };
  const setQuickFacts = (quickFacts: RpgAuthoringDossierQuickFact[]) => onChange({
    ...dossier,
    quick_facts: quickFacts,
  });

  return (
    <div className="rpg-dossier-section-editor">
      <section className="rpg-dossier-editor-fields">
        <header>
          <div><h5>Structured reading fields</h5><p>Edit the dossier as readable sections. Changes are not saved until Save Dossier Only is selected.</p></div>
          <span>{dossier.sections.length} sections</span>
        </header>

        <label>
          <span>Subtitle</span>
          <input
            value={dossier.subtitle ?? ''}
            onChange={(event) => onChange({ ...dossier, subtitle: event.currentTarget.value })}
            placeholder={`An evocative subtitle for ${entityTitle}`}
          />
        </label>

        <div className="rpg-dossier-editor-quote-fields">
          <label>
            <span>In-world quotation</span>
            <textarea
              rows={3}
              value={dossier.quote?.text ?? ''}
              onChange={(event) => onChange({
                ...dossier,
                quote: {
                  text: event.currentTarget.value,
                  attribution: dossier.quote?.attribution ?? '',
                },
              })}
            />
          </label>
          <label>
            <span>Attribution</span>
            <input
              value={dossier.quote?.attribution ?? ''}
              onChange={(event) => onChange({
                ...dossier,
                quote: {
                  text: dossier.quote?.text ?? '',
                  attribution: event.currentTarget.value,
                },
              })}
            />
          </label>
        </div>

        <details className="rpg-dossier-quick-fact-editor">
          <summary>Quick facts ({dossier.quick_facts.length})</summary>
          <div>
            {dossier.quick_facts.map((fact, index) => (
              <article key={`${fact.label}:${index}`}>
                <input
                  aria-label={`Quick fact ${index + 1} label`}
                  value={fact.label}
                  onChange={(event) => setQuickFacts(dossier.quick_facts.map((row, rowIndex) => (
                    rowIndex === index ? { ...row, label: event.currentTarget.value } : row
                  )))}
                  placeholder="Label"
                />
                <input
                  aria-label={`Quick fact ${index + 1} value`}
                  value={String(fact.value ?? '')}
                  onChange={(event) => setQuickFacts(dossier.quick_facts.map((row, rowIndex) => (
                    rowIndex === index ? { ...row, value: event.currentTarget.value } : row
                  )))}
                  placeholder="Value"
                />
                <button
                  aria-label={`Remove quick fact ${index + 1}`}
                  className="rpg-secondary-button"
                  type="button"
                  onClick={() => setQuickFacts(dossier.quick_facts.filter((_, rowIndex) => rowIndex !== index))}
                >
                  Remove
                </button>
              </article>
            ))}
            <button
              className="rpg-secondary-button"
              type="button"
              onClick={() => setQuickFacts([...dossier.quick_facts, { label: '', value: '' }])}
            >
              Add quick fact
            </button>
          </div>
        </details>

        <div className="rpg-dossier-editor-section-list">
          {dossier.sections.map((section, index) => (
            <article key={`${section.id}:${index}`}>
              <header>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <label>
                  <span>Section title</span>
                  <input
                    value={section.title}
                    onChange={(event) => updateSection(index, {
                      title: event.currentTarget.value,
                      id: section.id || slug(event.currentTarget.value, `section-${index + 1}`),
                    })}
                  />
                </label>
                <label>
                  <span>Stable anchor</span>
                  <input
                    value={section.id}
                    onChange={(event) => updateSection(index, { id: slug(event.currentTarget.value, `section-${index + 1}`) })}
                  />
                </label>
                <button
                  aria-label={`Remove ${section.title}`}
                  className="rpg-secondary-button"
                  type="button"
                  disabled={dossier.sections.length <= 1}
                  onClick={() => setSections(dossier.sections.filter((_, sectionIndex) => sectionIndex !== index))}
                >
                  Remove
                </button>
              </header>
              <div>
                {section.paragraphs.map((paragraph, paragraphIndex) => (
                  <label key={`${section.id}:paragraph:${paragraphIndex}`}>
                    <span>Paragraph {paragraphIndex + 1}</span>
                    <textarea
                      rows={5}
                      value={paragraph}
                      onChange={(event) => updateSection(index, {
                        paragraphs: section.paragraphs.map((value, rowIndex) => (
                          rowIndex === paragraphIndex ? event.currentTarget.value : value
                        )),
                      })}
                    />
                    <button
                      className="rpg-secondary-button"
                      type="button"
                      disabled={section.paragraphs.length <= 1}
                      onClick={() => updateSection(index, {
                        paragraphs: section.paragraphs.filter((_, rowIndex) => rowIndex !== paragraphIndex),
                      })}
                    >
                      Remove paragraph
                    </button>
                  </label>
                ))}
                <button
                  className="rpg-secondary-button"
                  type="button"
                  disabled={section.paragraphs.length >= 3}
                  onClick={() => updateSection(index, { paragraphs: [...section.paragraphs, ''] })}
                >
                  Add paragraph
                </button>
              </div>
            </article>
          ))}
          <button
            className="rpg-secondary-button"
            type="button"
            onClick={() => {
              const number = dossier.sections.length + 1;
              setSections([
                ...dossier.sections,
                {
                  id: `section-${number}`,
                  title: `New Section ${number}`,
                  paragraphs: [''],
                },
              ]);
            }}
          >
            Add section
          </button>
        </div>

        <label>
          <span>Related canonical entity IDs</span>
          <textarea
            rows={3}
            value={dossier.related_entity_ids.join('\n')}
            onChange={(event) => onChange({
              ...dossier,
              related_entity_ids: event.currentTarget.value
                .split(/\n|,/)
                .map((value) => value.trim())
                .filter(Boolean),
            })}
            placeholder={'faction:example\nlocation:example'}
          />
        </label>
      </section>

      <aside className="rpg-dossier-editor-preview" aria-label={`${entityTitle} dossier preview`}>
        <p className="eyebrow">Live preview</p>
        <h4>{entityTitle}</h4>
        {dossier.subtitle ? <p className="rpg-dossier-preview-subtitle">{dossier.subtitle}</p> : null}
        {dossier.quote?.text ? <blockquote>“{dossier.quote.text}”<cite>{dossier.quote.attribution}</cite></blockquote> : null}
        {dossier.sections.map((section) => (
          <section key={section.id}>
            <h5>{section.title || 'Untitled section'}</h5>
            {section.paragraphs.filter(Boolean).map((paragraph, index) => <p key={`${section.id}:${index}`}>{paragraph}</p>)}
          </section>
        ))}
      </aside>
    </div>
  );
}
