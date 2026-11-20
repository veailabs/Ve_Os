"""Bounded semantic observations for one document, including open shadow trees."""

SNAPSHOT = r"""(options) => {
  const opts = options || {};
  const state = window.__veosTargets ||= {ids: new WeakMap(), next: 0};
  const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const roots = [document], elements = [], texts = [];
  let scanned = 0, scanTruncated = false;
  for (let r = 0; r < roots.length; r++) {
    for (const el of roots[r].querySelectorAll('*')) {
      if (++scanned > 12000) { scanTruncated = true; break; }
      if (el.shadowRoot) roots.push(el.shadowRoot);
      if (el.matches('a,button,input:not([type=hidden]),textarea,select,[contenteditable=true],[role=button],[role=checkbox],[role=radio],[role=combobox],[role=textbox],[role=switch],[role=tab],[role=menuitem],[role=slider]') && visible(el)) elements.push(el);
      if (elements.length >= 2000) { scanTruncated = true; break; }
    }
    if (scanTruncated) break;
  }
  const name = el => {
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => el.getRootNode().getElementById?.(id)?.textContent || '').join(' ').trim();
    if (labelled) return labelled;
    const label = el.labels?.[0];
    let labelText = '';
    if (label) {
      const clone = label.cloneNode(true);
      clone.querySelectorAll('input,textarea,select,button').forEach(child => child.remove());
      labelText = clone.textContent || '';
    }
    return el.getAttribute('aria-label') || labelText.trim() || el.innerText || el.getAttribute('alt') || el.title || el.name || el.getAttribute('placeholder') || '';
  };
  const controls = elements.map(el => {
    if (!state.ids.has(el)) state.ids.set(el, String(state.next++));
    const id = state.ids.get(el); el.setAttribute('data-veos-id', id);
    const sensitive = el.type === 'password' || /password|cc-|one-time-code/i.test(el.autocomplete || '')
      || /password|verification.?code|secret|api.?key|access.?token/i.test([el.name, el.id, el.getAttribute('aria-label')].join(' '));
    const control = {id, tag: el.tagName.toLowerCase(), type: el.type || '',
      role: el.getAttribute('role') || '', name: name(el).slice(0,300),
      href: el.tagName === 'A' ? el.href : '', target: el.getAttribute('target') || '', disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      sensitive, editable: el.isContentEditable, readonly: !!el.readOnly};
    if ('checked' in el || el.hasAttribute('aria-checked')) control.checked = 'checked' in el ? el.checked : el.getAttribute('aria-checked');
    if (el.hasAttribute('aria-expanded')) control.expanded = el.getAttribute('aria-expanded');
    if (el.hasAttribute('aria-selected')) control.selected = el.getAttribute('aria-selected');
    if (!sensitive && (['INPUT','TEXTAREA','SELECT'].includes(el.tagName) || el.isContentEditable))
      control.value = String(el.isContentEditable ? el.innerText : el.value).slice(0,10000);
    if (el.tagName === 'SELECT') {
      control.options = [...el.options].slice(0,100).map(o => ({value: o.value.slice(0,300), label: o.label.slice(0,300), selected: o.selected, disabled: o.disabled}));
      control.options_truncated = el.options.length > 100;
    }
    return control;
  });
  // Values remain part of the revision even if a targeted observation omits the field.
  let valueBudget = 200000;
  const fieldValues = elements.map((el, i) => {
    const value = controls[i].sensitive ? '[manual]' : String(el.isContentEditable ? el.innerText : el.value ?? '');
    if (value.length > 10000 || value.length > valueBudget) scanTruncated = true;
    const bounded = value.slice(0,Math.min(10000, Math.max(0,valueBudget)));
    valueBudget -= bounded.length;
    return [controls[i].id, bounded, el.checked ?? null];
  });
  const query = String(opts.query || '').toLowerCase();
  const matching = controls.filter(c => !query || [c.name,c.role,c.tag].join(' ').toLowerCase().includes(query));
  const offset = opts.control_offset || 0, limit = opts.control_limit || 100;
  texts.push((document.body?.innerText || '').slice(0,1000000));
  for (const root of roots.slice(1)) {
    const visibleText = [...root.children].filter(visible).map(el => el.innerText || '').join('\n');
    texts.push(visibleText.slice(0,100000));
  }
  const text = texts.join('\n'), textOffset = opts.text_offset || 0, textLimit = opts.text_limit || 12000;
  // Bind actionable state, including form text, rather than unrelated live status text.
  const forms = [...document.querySelectorAll('form,[role=form]')].slice(0,50)
    .map(form => (form.innerText || '').slice(0,20000));
  return {url: location.href, title: document.title,
    text: text.slice(textOffset,textOffset+textLimit),
    text_offset: textOffset, next_text_offset: textOffset+textLimit < text.length ? textOffset+textLimit : null,
    controls: matching.slice(offset,offset+limit), control_offset: offset,
    total_controls: matching.length, next_control_offset: offset+limit < matching.length ? offset+limit : null,
    truncated: scanTruncated || offset+limit < matching.length || textOffset+textLimit < text.length,
    scan_truncated: scanTruncated, has_sensitive_controls: controls.some(c => c.sensitive),
    binding: {url: location.href, controls, fieldValues, forms}, fieldValues};
}"""
