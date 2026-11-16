(function () {
  'use strict';
  var catalog = { domains: [], signatures: [], groups: [], rules: [] };
  var editor = null;
  var signatureModal;
  var groupModal;
  var ruleModal;
  var domainModal;
  var editingGroupMembers = {};
  var editingRuleConditions = [];
  var $ = function (id) { return document.getElementById(id); };

  function node(tag, cls, value) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (value !== undefined) el.textContent = value;
    return el;
  }

  function notify(message, type) {
    var el = $('osw-alert');
    el.className = 'alert alert-' + (type || 'info');
    el.textContent = message;
    el.classList.remove('d-none');
  }

  async function api(method, path, payload) {
    var response = await fetch('/inc/ajax/opensigweave.php', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': oswCsrfToken },
      body: JSON.stringify({ method: method, path: path, payload: payload || {} })
    });
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.detail || ('Request failed: ' + response.status));
    return data;
  }

  function fillSelect(id, values, labelKey, valueKey, selected) {
    var select = $(id);
    select.replaceChildren();
    values.forEach(function (item) {
      var option = node('option', '', item[labelKey]);
      option.value = String(item[valueKey]);
      option.selected = !!selected && selected.indexOf(String(item[valueKey])) !== -1;
      select.append(option);
    });
  }

  function selectedValues(id) {
    return Array.from($(id).selectedOptions).map(function (option) { return option.value; });
  }

  function renderDomains() {
    var root = $('osw-domains');
    root.replaceChildren();
    if (!catalog.domains.length) {
      root.append(node('div', 'text-muted', 'Noch keine Domains konfiguriert.'));
      return;
    }
    catalog.domains.forEach(function (domain) {
      var row = node('div', 'osw-domain-row');
      row.append(node('strong', '', domain.domain_name));
      row.append(node('span', domain.is_active ? 'badge text-bg-success' : 'badge text-bg-secondary',
        domain.is_active ? 'Aktiv' : 'Inaktiv'));
      var edit = node('button', 'btn btn-sm btn-outline-light ms-auto', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', function () { openDomain(domain); });
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', async function () {
        if (!window.confirm('Domain ' + domain.domain_name + ' löschen? Zugeordnete Signaturen oder Gruppen müssen zuerst entfernt werden.')) return;
        try {
          await api('DELETE', '/admin/domains/' + domain.id);
          await refresh();
          notify('Domain gelöscht.', 'success');
        } catch (error) { notify(error.message, 'danger'); }
      });
      row.append(edit, remove);
      root.append(row);
    });
  }

  function openDomain(domain) {
    $('osw-domain-id').value = domain ? String(domain.id) : '';
    $('osw-domain-title').textContent = domain ? 'Domain bearbeiten' : 'Domain hinzufügen';
    $('osw-domain-name').value = domain ? domain.domain_name : '';
    $('osw-domain-active').checked = domain ? !!domain.is_active : true;
    $('osw-domain-overrides').checked = domain ? !!domain.allow_overrides : true;
    $('osw-domain-replies').checked = domain ? !!domain.inject_on_replies : false;
    $('osw-domain-trim').checked = domain ? !!domain.trim_whitespace : true;
    $('osw-domain-strip-device').checked = domain ? !!domain.strip_device_signatures : false;
    domainModal.show();
  }

  async function saveDomain() {
    var id = $('osw-domain-id').value;
    var payload = {
      domain_name: $('osw-domain-name').value.trim(),
      is_active: $('osw-domain-active').checked,
      allow_overrides: $('osw-domain-overrides').checked,
      inject_on_replies: $('osw-domain-replies').checked,
      trim_whitespace: $('osw-domain-trim').checked,
      strip_device_signatures: $('osw-domain-strip-device').checked
    };
    if (!payload.domain_name) {
      notify('Bitte einen Domainnamen eingeben.', 'warning');
      return;
    }
    var button = $('osw-save-domain');
    button.disabled = true;
    try {
      await api(id ? 'PUT' : 'POST', id ? '/admin/domains/' + id : '/admin/domains', payload);
      domainModal.hide();
      await refresh();
      notify(id ? 'Domain aktualisiert.' : 'Domain hinzugefügt.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
    finally { button.disabled = false; }
  }

  function renderSignatures() {
    var root = $('osw-signatures');
    root.replaceChildren();
    $('osw-signature-count').textContent = String(catalog.signatures.length);
    if (!catalog.signatures.length) {
      var empty = node('tr');
      var cell = node('td', 'text-muted text-center py-4', 'Noch keine Signaturen angelegt.');
      cell.colSpan = 5;
      empty.append(cell);
      root.append(empty);
      return;
    }
    catalog.signatures.forEach(function (signature) {
      var row = node('tr');
      row.append(node('td', 'fw-semibold', signature.name));
      row.append(node('td', '', signature.mode + ' · Gewicht ' + signature.weight));
      row.append(node('td', 'text-muted small', signature.domains.length ? signature.domains.join(', ') : 'Global'));
      row.append(node('td', '', signature.is_active ? 'Aktiv' : 'Deaktiviert'));
      var actions = node('td', 'text-end');
      var edit = node('button', 'btn btn-sm btn-outline-light me-2', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', function () { openSignature(signature); });
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', function () { deleteSignature(signature); });
      actions.append(edit, remove);
      row.append(actions);
      root.append(row);
    });
  }

  function renderGroups() {
    var root = $('osw-groups');
    root.replaceChildren();
    if (!catalog.groups.length) {
      root.append(node('div', 'text-muted', 'Noch keine Gruppen angelegt.'));
      return;
    }
    catalog.groups.forEach(function (group) {
      var row = node('div', 'osw-domain-row');
      var info = node('div');
      info.append(node('strong', '', group.name));
      info.append(node('div', 'small text-muted', group.members.length + ' Signaturen · ' +
        (group.domains.length ? group.domains.join(', ') : 'alle Domains')));
      var edit = node('button', 'btn btn-sm btn-outline-light me-2', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', function () { openGroup(group); });
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', async function () {
        if (!window.confirm('Gruppe "' + group.name + '" löschen?')) return;
        try { await api('DELETE', '/admin/groups/' + group.id); await refresh(); notify('Gruppe gelöscht.', 'success'); }
        catch (error) { notify(error.message, 'danger'); }
      });
      row.append(info, edit, remove);
      root.append(row);
    });
  }

  function renderRules() {
    var root = $('osw-rules');
    root.replaceChildren();
    if (!catalog.rules.length) {
      root.append(node('div', 'text-muted', 'Noch keine Regeln angelegt.'));
      return;
    }
    catalog.rules.forEach(function (rule) {
      var row = node('div', 'osw-domain-row');
      var info = node('div');
      info.append(node('strong', '', rule.name));
      info.append(node('div', 'small text-muted', 'Priorität ' + rule.priority + ' · ' +
        rule.action_mode + ' · ' + rule.conditions.length + ' Bedingungen'));
      var edit = node('button', 'btn btn-sm btn-outline-light me-2', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', function () { openRule(rule); });
      var remove = node('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', async function () {
        if (!window.confirm('Regel "' + rule.name + '" löschen?')) return;
        try { await api('DELETE', '/admin/rules/' + rule.id); await refresh(); notify('Regel gelöscht.', 'success'); }
        catch (error) { notify(error.message, 'danger'); }
      });
      row.append(info, edit, remove);
      root.append(row);
    });
  }

  async function refresh() {
    try {
      var results = await Promise.all([
        api('GET', '/admin/catalog'),
        api('GET', '/admin/groups'),
        api('GET', '/admin/rules')
      ]);
      catalog = { domains: results[0].domains, signatures: results[0].signatures,
        groups: results[1], rules: results[2] };
      renderDomains();
      renderSignatures();
      renderGroups();
      renderRules();
    } catch (error) {
      notify(error.message, 'danger');
    }
  }

  function openSignature(signature) {
    $('osw-signature-id').value = signature ? signature.id : '';
    $('osw-modal-title').textContent = signature ? 'Signatur bearbeiten' : 'Neue Signatur';
    $('osw-signature-name').value = signature ? signature.name : '';
    $('osw-signature-mode').value = signature ? signature.mode : 'AUTO';
    $('osw-signature-weight').value = signature ? signature.weight : 1;
    var domainSelect = $('osw-signature-domains');
    domainSelect.replaceChildren();
    catalog.domains.forEach(function (domain) {
      var option = node('option', '', domain.domain_name);
      option.value = String(domain.id);
      option.selected = !!signature && signature.domains.indexOf(domain.domain_name) !== -1;
      domainSelect.append(option);
    });
    if (editor) {
      editor.setData(signature ? signature.html_content :
        '<table><tbody><tr><td>{{ first_name }} {{ last_name }}</td></tr><tr><td>{{ title }}</td></tr><tr><td>{{ phone }}</td></tr><tr><td>{{ email }}</td></tr></tbody></table>');
    }
    signatureModal.show();
  }

  async function saveSignature() {
    var id = $('osw-signature-id').value;
    var payload = {
      name: $('osw-signature-name').value.trim(),
      mode: $('osw-signature-mode').value,
      weight: Math.max(1, Number($('osw-signature-weight').value) || 1),
      domain_ids: Array.from($('osw-signature-domains').selectedOptions).map(function (o) { return Number(o.value); }),
      html_content: editor ? editor.getData() : ''
    };
    if (!payload.name) { notify('Bitte einen Signaturnamen eingeben.', 'warning'); return; }
    var button = $('osw-save-signature');
    button.disabled = true;
    try {
      await api(id ? 'PUT' : 'POST', id ? '/admin/signatures/' + id : '/admin/signatures', payload);
      signatureModal.hide();
      await refresh();
      notify('Signatur gespeichert.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
    finally { button.disabled = false; }
  }

  async function deleteSignature(signature) {
    if (!window.confirm('Signatur "' + signature.name + '" wirklich löschen?')) return;
    try {
      await api('DELETE', '/admin/signatures/' + signature.id);
      await refresh();
      notify('Signatur gelöscht.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
  }

  async function saveGroup() {
    var groupId = $('osw-group-id').value;
    var payload = {
      name: $('osw-group-name').value.trim(),
      domain_ids: selectedValues('osw-group-domains').map(Number),
      members: Array.from($('osw-group-member-list').querySelectorAll('[data-signature-id]'))
        .filter(function (entry) { return entry.querySelector('[data-member-selected]').checked; })
        .map(function (entry) {
          var id = entry.getAttribute('data-signature-id');
          var current = editingGroupMembers[id];
          return {
            signature_id: id,
            weight: Math.max(1, Number(entry.querySelector('[data-member-weight]').value) || 1),
            is_enabled: current ? current.is_enabled : true
          };
        })
    };
    if (!payload.name || !payload.members.length) {
      notify('Bitte einen Gruppennamen und mindestens eine Signatur auswählen.', 'warning');
      return;
    }
    try {
      await api(groupId ? 'PUT' : 'POST', groupId ? '/admin/groups/' + groupId : '/admin/groups', payload);
      groupModal.hide();
      await refresh();
      notify('Gruppe gespeichert.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
  }

  async function saveRule() {
    var ruleId = $('osw-rule-id').value;
    var action = $('osw-rule-action').value;
    var condition = $('osw-rule-condition').value;
    var value = $('osw-rule-value').value.trim();
    var payload = {
      name: $('osw-rule-name').value.trim(),
      priority: Number($('osw-rule-priority').value) || 0,
      action_mode: action,
      signature_id: $('osw-rule-signature').value || null,
      group_id: action === 'GROUP' ? ($('osw-rule-group').value || null) : null,
      is_enabled: true,
      conditions: condition && value ? (function () {
        var next = editingRuleConditions.slice();
        var first = { kind: condition, key: $('osw-rule-key').value.trim(), value: value };
        if (next.length) next[0] = first; else next.push(first);
        return next;
      })() : []
    };
    if (!payload.name || (action === 'GROUP' && !payload.group_id) || (condition && !value)) {
      notify('Bitte Regelname, Gruppenaktion und Bedingungswert prüfen.', 'warning');
      return;
    }
    try {
      await api(ruleId ? 'PUT' : 'POST', ruleId ? '/admin/rules/' + ruleId : '/admin/rules', payload);
      ruleModal.hide();
      await refresh();
      notify('Regel gespeichert.', 'success');
    } catch (error) { notify(error.message, 'danger'); }
  }

  function openGroup(group) {
    $('osw-group-id').value = group ? group.id : '';
    $('osw-group-title').textContent = group ? 'Signaturgruppe bearbeiten' : 'Signaturgruppe anlegen';
    $('osw-group-name').value = group ? group.name : '';
    editingGroupMembers = {};
    if (group) group.members.forEach(function (member) { editingGroupMembers[member.signature_id] = member; });
    var memberList = $('osw-group-member-list');
    memberList.replaceChildren();
    catalog.signatures.forEach(function (signature) {
      var current = editingGroupMembers[signature.id];
      var row = node('div', 'd-flex align-items-center justify-content-between gap-3 border rounded p-2 mb-2');
      row.setAttribute('data-signature-id', String(signature.id));
      var check = node('input', 'form-check-input me-2');
      check.type = 'checkbox';
      check.checked = !!current;
      check.setAttribute('data-member-selected', 'true');
      var label = node('label', 'form-check-label', signature.name);
      label.htmlFor = 'osw-group-member-' + signature.id;
      check.id = label.htmlFor;
      var left = node('div', 'form-check mb-0');
      left.append(check, label);
      var weight = node('input', 'form-control form-control-sm');
      weight.type = 'number';
      weight.min = '1';
      weight.max = '100000';
      weight.value = String(current ? current.weight : 1);
      weight.style.width = '110px';
      weight.setAttribute('aria-label', 'Gewichtung für ' + signature.name);
      weight.setAttribute('data-member-weight', 'true');
      row.append(left, weight);
      memberList.append(row);
    });
    fillSelect('osw-group-domains', catalog.domains, 'domain_name', 'id',
      group ? catalog.domains.filter(function (d) { return group.domains.indexOf(d.domain_name) !== -1; }).map(function (d) { return d.id; }) : []);
    groupModal.show();
  }

  function openRule(rule) {
    $('osw-rule-id').value = rule ? rule.id : '';
    editingRuleConditions = rule ? rule.conditions.slice() : [];
    $('osw-rule-title').textContent = rule ? 'Regel bearbeiten' : 'Regel anlegen';
    $('osw-rule-name').value = rule ? rule.name : '';
    $('osw-rule-priority').value = rule ? String(rule.priority) : '0';
    $('osw-rule-action').value = rule ? rule.action_mode : 'AUTO';
    $('osw-rule-key').value = rule && rule.conditions.length ? rule.conditions[0].key : '';
    $('osw-rule-value').value = rule && rule.conditions.length ? rule.conditions[0].value : '';
    $('osw-rule-condition').value = rule && rule.conditions.length ? rule.conditions[0].kind : '';
    fillSelect('osw-rule-signature', [{ id: '', name: 'Keine' }].concat(catalog.signatures), 'name', 'id',
      rule ? [rule.signature_id || ''] : ['']);
    fillSelect('osw-rule-group', [{ id: '', name: 'Bitte auswählen' }].concat(catalog.groups), 'name', 'id',
      rule ? [rule.group_id || ''] : ['']);
    ruleModal.show();
  }

  document.addEventListener('DOMContentLoaded', async function () {
    signatureModal = new bootstrap.Modal($('osw-signature-modal'));
    domainModal = new bootstrap.Modal($('osw-domain-modal'));
    groupModal = new bootstrap.Modal($('osw-group-modal'));
    ruleModal = new bootstrap.Modal($('osw-rule-modal'));
    try {
      editor = await ClassicEditor.create($('osw-signature-html'), {
        toolbar: ['undo', 'redo', '|', 'heading', 'bold', 'italic', '|', 'link', 'bulletedList', 'numberedList', '|', 'insertTable', 'blockQuote']
      });
    } catch (error) {
      notify('CKEditor 5 konnte nicht geladen werden: ' + error.message, 'danger');
    }
    $('osw-refresh').addEventListener('click', refresh);
    $('osw-new-signature').addEventListener('click', function () { openSignature(null); });
    $('osw-new-domain').addEventListener('click', function () { openDomain(null); });
    $('osw-save-domain').addEventListener('click', saveDomain);
    $('osw-save-signature').addEventListener('click', saveSignature);
    $('osw-new-group').addEventListener('click', function () { openGroup(null); });
    $('osw-save-group').addEventListener('click', saveGroup);
    $('osw-new-rule').addEventListener('click', function () { openRule(null); });
    $('osw-save-rule').addEventListener('click', saveRule);
    await refresh();
  });
})();
