import { serve, callHost } from '/runtime/block-sdk.js';

const STORAGE_KEY = 'format';
const SETTINGS_PAGE_SURFACE = 'settings_page';
const FORMATS = ['absolute', 'relative', 'name'];
const DEFAULT_FORMAT = 'absolute';
const COPY_COMMANDS = new Set(['copy_path']);
const CHINESE_TEXT = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/;

const FORMAT_LABELS = {
  absolute: ['完整路径', 'Full path'],
  relative: ['相对路径', 'Relative path'],
  name: ['文件名', 'File name'],
};

let locale = 'zh';
let format = DEFAULT_FORMAT;
let lastFile = '';
let lastRel = '';
let downloadsDir = '';

function isEn() {
  return String(locale || '').toLowerCase().startsWith('en');
}

function tr(zh, en) {
  return isEn() ? en : zh;
}

function localizeError(message) {
  const text = String(message || '').trim();
  if (isEn() && CHINESE_TEXT.test(text)) {
    return 'Operation failed';
  }
  return text || tr('操作失败', 'Operation failed');
}

function cleanFormat(value) {
  const text = String(value ?? '').trim();
  return FORMATS.includes(text) ? text : DEFAULT_FORMAT;
}

function basename(absPath) {
  const trimmed = absPath.replace(/[/\\]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'));
  const name = idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
  return name || absPath;
}

function renderPath(fmt, absPath, relPath) {
  if (fmt === 'relative' && relPath) {
    return relPath;
  }
  if (fmt === 'name') {
    return basename(absPath);
  }
  return absPath;
}

function formatLabel(fmt) {
  const pair = FORMAT_LABELS[fmt];
  return tr(pair[0], pair[1]);
}

function deriveHomeDir(dir) {
  const clean = String(dir || '').trim().replace(/[/\\]+$/, '');
  if (!clean) {
    return '';
  }
  const idx = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'));
  if (idx > 0 && clean.slice(idx + 1).toLowerCase() === 'downloads') {
    return clean.slice(0, idx);
  }
  return '';
}

function samplePaths() {
  const [notebook, rel] = isEn()
    ? ['Workspace', 'Specs/api-design.md']
    : ['团队文档', '架构/同步协议.md'];
  const home = deriveHomeDir(downloadsDir) || '~';
  const sep = home.includes('\\') && !home.includes('/') ? '\\' : '/';
  const prefix = home.endsWith(sep) ? home.slice(0, -1) : home;
  const absPath = [prefix, 'Documents', notebook, ...rel.split('/')].join(sep);
  return [absPath, rel];
}

function buildPage() {
  const [defaultFile, defaultRel] = samplePaths();
  const sampleFile = lastFile || defaultFile;
  const sampleRel = lastFile ? lastRel : defaultRel;
  const preview = renderPath(format, sampleFile, sampleRel);
  const items = [
    {
      kind: 'segmented',
      id: 'format',
      label: tr('复制内容', 'Copy as'),
      value: format,
      options: FORMATS.map((f) => ({ value: f, label: formatLabel(f) })),
    },
    {
      kind: 'kv',
      rows: [{ label: tr('效果预览', 'Preview'), value: preview }],
    },
  ];
  if (format === 'relative') {
    items.push({
      kind: 'kv',
      rows: [
        {
          label: tr('笔记本外的文件', 'Files outside notebooks'),
          value: tr('复制完整路径', 'Copy the full path'),
        },
      ],
    });
  }
  return {
    title: tr('复制路径', 'Copy Path'),
    cards: [
      {
        id: 'copy-format',
        title: tr('格式', 'Format'),
        items,
      },
    ],
  };
}

function saveFormat(value) {
  const fmt = cleanFormat(value);
  if (fmt !== format) {
    format = fmt;
    callHost('host.storage.set', { key: STORAGE_KEY, value: fmt });
  }
}

function copyTargetPath(context) {
  const absPath = String(context?.file || '').trim();
  if (!absPath) {
    return {
      ok: false,
      tone: 'warning',
      status: tr('没有可复制的文件路径', 'No file path to copy'),
    };
  }
  const relPath = String(context?.note || context?.folder || '').trim();
  lastFile = absPath;
  lastRel = relPath;
  const text = renderPath(format, absPath, relPath);
  callHost('host.clipboard.write', { text });
  return {
    ok: true,
    tone: 'success',
    status: tr('已复制', 'Copied ') + formatLabel(format).toLowerCase(),
  };
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function handle(method, params) {
  if (method === 'plugin.initialize') {
    locale = String(params.locale || 'zh');
    downloadsDir = String(params.downloads_dir || '');
    const stored = callHost('host.storage.get', { key: STORAGE_KEY });
    if (isPlainObject(stored) && stored.found) {
      format = cleanFormat(stored.value);
    }
    return { ready: true };
  }

  if (method === 'plugin.render') {
    const surface = String(params.surface || SETTINGS_PAGE_SURFACE);
    if (surface !== SETTINGS_PAGE_SURFACE) {
      throw new Error(tr(`不支持的界面: ${surface}`, `Unsupported surface: ${surface}`));
    }
    const values = params.values;
    const accepted = {};
    if (isPlainObject(values) && Object.prototype.hasOwnProperty.call(values, 'format')) {
      saveFormat(values.format);
      accepted.format = format;
    } else if (!values || (isPlainObject(values) && Object.keys(values).length === 0)) {
      accepted.format = format;
    }
    return { page: buildPage(), values: accepted };
  }

  if (method === 'plugin.action') {
    return {
      ok: false,
      tone: 'warning',
      status: tr('未知操作', 'Unknown action'),
      page: buildPage(),
    };
  }

  if (method === 'plugin.command') {
    const commandId = String(params.command_id || '');
    if (!COPY_COMMANDS.has(commandId)) {
      throw new Error(tr(`未知命令: ${commandId}`, `Unknown command: ${commandId}`));
    }
    const context = isPlainObject(params.context) ? params.context : {};
    return copyTargetPath(context);
  }

  if (method === 'plugin.event') {
    if (params.event === 'locale.changed' && params.locale) {
      locale = String(params.locale);
      return { handled: true };
    }
    return { handled: false };
  }

  if (method === 'plugin.shutdown') {
    return { ok: true };
  }

  throw new Error(tr(`不支持的方法: ${method}`, `Unsupported method: ${method}`));
}

serve((method, params) => {
  try {
    return handle(method, params);
  } catch (err) {
    const localized = new Error(localizeError(err && err.message ? err.message : err));
    if (err && typeof err.code === 'number') {
      localized.code = err.code;
    }
    throw localized;
  }
});
