(function () {
  'use strict';

  var allowedPlugins = [
    'Essentials', 'Paragraph', 'Heading', 'Bold', 'Italic', 'Underline',
    'Font', 'Link', 'List', 'Alignment', 'Table', 'TableToolbar',
    'GeneralHtmlSupport', 'PasteFromOffice', 'BlockQuote', 'HorizontalLine'
  ];

  var allowedElements = [
    'p', 'div', 'center', 'span', 'br', 'strong', 'b', 'em', 'i', 'u', 's',
    'small', 'sub', 'sup', 'code', 'pre', 'a', 'img', 'font',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th',
    'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'hr'
  ];

  async function create(element, options) {
    var config = options || {};
    var library = window.CKEDITOR;
    if (!library || !library.ClassicEditor) {
      throw new Error('CKEditor 5 konnte nicht geladen werden.');
    }

    var plugins = allowedPlugins.map(function (name) { return library[name]; }).filter(Boolean);
    var editor = await library.ClassicEditor.create(element, {
      licenseKey: 'GPL',
      plugins: plugins,
      toolbar: [
        'undo', 'redo', '|', 'heading', '|',
        'fontFamily', 'fontSize', 'fontColor', 'fontBackgroundColor', '|',
        'bold', 'italic', 'underline', 'link', '|',
        'bulletedList', 'numberedList', '|', 'alignment', 'insertTable', '|',
        'blockQuote', 'horizontalLine'
      ],
      table: {
        contentToolbar: ['tableColumn', 'tableRow', 'mergeTableCells']
      },
      htmlSupport: {
        allow: [{
          name: new RegExp('^(' + allowedElements.join('|') + ')$'),
          attributes: [
            'style', 'class', 'href', 'src', 'alt', 'title', 'width', 'height',
            'align', 'valign', 'border', 'cellpadding', 'cellspacing',
            'colspan', 'rowspan', 'target', 'rel', 'dir', 'role', 'id',
            'face', 'color', 'size', 'start', 'type', 'value'
          ],
          classes: true,
          styles: true
        }]
      },
      link: { addTargetToExternalLinks: false },
      initialData: typeof config.initialData === 'string' ? config.initialData : '',
      placeholder: config.placeholder || 'E-Mail-Signatur erstellen …'
    });

    return {
      getData: function () { return editor.getData(); },
      setData: function (html) { editor.setData(typeof html === 'string' ? html : ''); },
      focus: function () { editor.editing.view.focus(); },
      destroy: function () { return editor.destroy(); }
    };
  }

  window.OpenSigWeaveEditor = { create: create };
})();
