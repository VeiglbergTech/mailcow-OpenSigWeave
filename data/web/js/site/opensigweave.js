(function () {
  'use strict';
  const byId = (id) => document.getElementById(id);
  let catalog = { domains: [], signatures: [] };
  let editor = null;
  let modal = null;

  function alertMessage(message, type) {
    const node = byId('osw-alert');
    node.className = 'alert alert-' + (type || 'info');
    node.textContent = message;
    node.classList.remove('d-none');
  }

  async function api(method, path, payload) {
    const response = await fetch('/inc/ajax/opensigweave.php', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': oswCsrfToken },
      body: JSON.stringify({ method, path, payload: payload || {} })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'OpenSigWeave request failed (' + response.status + ')');
    return data;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderDomains() {
    const root = byId('osw-domains');
    root.replaceChildren();
    if (!catalog.domains.length) {
      root.append(element('div', 'text-muted', 'Noch keine Domains konfiguriert.'));
      return;
    }
    catalog.domains.forEach((domain) => {
      const row = element('div', 'osw-domain-row');
      const name = element('strong', '', domain.domain_name);
      const status = element('span', domain.is_active ? 'badge text-bg-success' : 'badge text-bg-secondary',
        domain.is_active ? 'Aktiv' : 'Inaktiv');
      row.append(name, status);
      root.append(row);
    });
  }

  function renderSignatures() {
    const root = byId('osw-signatures');
    root.replaceChildren();
    byId('osw-signature-count').textContent = String(catalog.signatures.length);
    if (!catalog.signatures.length) {
      const tr = element('tr');
      const td = element('td', 'text-muted text-center py-4', 'Noch keine Signaturen angelegt.');
      td.colSpan = 5;
      tr.append(td);
      root.append(tr);
      return;
    }
    catalog.signatures.forEach((signature) => {
      const tr = element('tr');
      tr.append(element('td', 'fw-semibold', signature.name));
      tr.append(element('td', '', signature.mode));
      tr.append(element('td', 'text-muted small', signature.domains.length ? signature.domains.join(', ') : 'Global'));
      tr.append(element('td', '', signature.is_active ? 'Aktiv' : 'Deaktiviert'));
      const actions = element('td', 'text-end');
      const edit = element('button', 'btn btn-sm btn-outline-light me-2', 'Bearbeiten');
      edit.type = 'button';
      edit.addEventListener('click', () => openEditor(signature));
      const remove = element('button', 'btn btn-sm btn-outline-danger', 'Löschen');
      remove.type = 'button';
      remove.addEventListener('click', () => deleteSignature(signature));
      actions.append(edit, remove);
      tr.append(actions);
      root.append(tr);
    });
  }

  async function refresh() {
    try {
      catalog = await api('GET', '/admin/catalog');
      renderDomains();
      renderSignatures();
    } catch (error) {
      alertMessage(error.message, 'danger');
    }
  }

  function renderDomainOptions(selectedNames) {
    const select = byId('osw-signature-domains');
    select.replaceChildren();
    catalog.domains.forEach((domain) => {
      const option = element('option', '', domain.domain_name);
      option.value = String(domain.id);
      option.selected = selectedNames.includes(domain.domain_name);
      select.append(option);
    });
  }

  function selectedDomainIds() {
    return Array.from(byId('osw-signature-domains').selectedOptions).map((option) => Number(option.value));
  }

  function openEditor(signature) {
    byId('osw-signature-id').value = signature ? signature.id : '';
    byId('osw-modal-title').textContent = signature ? 'Signatur bearbeiten' : 'Neue Signatur';
    byId('osw-signature-name').value = signature ? signature.name : '';
    byId('osw-signature-mode').value = signature ? signature.mode : 'AUTO';
    renderDomainOptions(signature ? signature.domains : []);
    if (editor) editor.setData(signature ? signature.html_content : '<table><tbody><tr><td>{{ first_name }} {{ last_name }}</td></tr><tr><td>{{ title }}</td></tr><tr><td>{{ phone }}</td></tr><tr><td>{{ email }}</td></tr></tbody></table>');
    modal.show();
  }

  async function deleteSignature(signature) {
    if (!window.confirm('Signatur „' + signature.name + '“ wirklich löschen?')) return;
    try {
      await api('DELETE', '/admin/signatures/' + signature.id);
      alertMessage('Signatur wurde gelöscht.', 'success');
      await refresh();
    } catch (error) {
      alertMessage(error.message, 'danger');
    }
  }

  async function saveSignature() {
    const id = byId('osw-signature-id').value;
    const payload = {
      name: byId('osw-signature-name').value.trim(),
      mode: byId('osw-signature-mode').value,
      domain_ids: selectedDomainIds(),
      html_content: editor ? editor.getData() : ''
    };
    if (!payload.name) {
      alertMessage('Bitte einen Signaturnamen eingeben.', 'warning');
      return;
    }
    const button = byId('osw-save-signature');
    button.disabled = true;
    try {
      await api(id ? 'PUT' : 'POST', id ? '/admin/signatures/' + id : '/admin/signatures', payload);
      modal.hide();
      alertMessage('Signatur wurde gespeichert.', 'success');
      await refresh();
    } catch (error) {
      alertMessage(error.message, 'danger');
    } finally {
      button.disabled = false;
    }
  }

  async function addDomain() {
    const name = window.prompt('Domainname, z. B. example.org:');
    if (!name) return;
    try {
      await api('POST', '/admin/domains', { domain_name: name.trim() });
      alertMessage('Domain wurde hinzugefügt.', 'success');
      await refresh();
    } catch (error) {
      alertMessage(error.message, 'danger');
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    modal = new bootstrap.Modal(byId('osw-signature-modal'));
    try {
      editor = await ClassicEditor.create(byId('osw-signature-html'), {
        toolbar: ['undo', 'redo', '|', 'heading', 'bold', 'italic', '|', 'link', 'bulletedList', 'numberedList', '|', 'insertTable', 'blockQuote']
      });
    } catch (error) {
      alertMessage('CKEditor 5 konnte nicht geladen werden: ' + error.message, 'danger');
    }
    byId('osw-refresh').addEventListener('click', refresh);
    byId('osw-new-signature').addEventListener('click', () => openEditor(null));
    byId('osw-new-domain').addEventListener('click', addDomain);
    byId('osw-save-signature').addEventListener('click', saveSignature);
    await refresh();
  });
})();
