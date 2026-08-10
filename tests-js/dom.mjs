const escape = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

class DomNode {
  constructor() {
    this.parentNode = null;
    this.childNodes = [];
  }

  append(...children) {
    children.flat().forEach((child) => {
      if (!child) return;
      this.childNodes.push(child);
      child.parentNode = this;
    });
  }

  replaceChildren(...children) {
    this.childNodes = [];
    this.append(...children);
  }

  get textContent() {
    return this.childNodes.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this.replaceChildren(new DomText(value));
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      node.childNodes?.forEach((child) => {
        if (child.matches?.(selector)) matches.push(child);
        visit(child);
      });
    };
    visit(this);
    return matches;
  }
}

class DomText extends DomNode {
  constructor(value) {
    super();
    this.value = String(value);
  }

  get textContent() {
    return this.value;
  }

  set textContent(value) {
    this.value = String(value);
  }
}

const matchesSelector = (element, selector) => {
  const tag = selector.match(/^[\w-]+/)?.[0];
  if (tag && element.tagName !== tag.toLowerCase()) return false;
  const classes = [...selector.matchAll(/\.([\w-]+)/g)].map((match) => match[1]);
  if (classes.some((name) => !element.className.split(/\s+/).includes(name))) return false;
  for (const attribute of selector.matchAll(/\[([^=\^]+)(\^=|=)?(?:"([^"]*)"|'([^']*)'|([^\]]*))?\]/g)) {
    const actual = element.getAttribute(attribute[1]);
    if (actual === null) return false;
    const expected = attribute[3] ?? attribute[4] ?? attribute[5];
    if (expected && (attribute[2] === "^=" ? !actual.startsWith(expected) : actual !== expected)) return false;
  }
  return Boolean(tag || classes.length || selector.includes("["));
};

class DomElement extends DomNode {
  constructor(tagName) {
    super();
    this.tagName = tagName.toLowerCase();
    this.attributes = new Map();
    this.listeners = new Map();
    this.value = "";
  }

  get className() {
    return this.getAttribute("class") || "";
  }

  set className(value) {
    this.setAttribute("class", value);
  }

  get href() {
    return this.getAttribute("href") || "";
  }

  set href(value) {
    this.setAttribute("href", value);
  }

  get src() {
    return this.getAttribute("src") || "";
  }

  set src(value) {
    this.setAttribute("src", value);
  }

  get innerHTML() {
    return this.childNodes.map((child) => child instanceof DomText ? escape(child.value) : child.outerHTML).join("");
  }

  get outerHTML() {
    const attrs = [...this.attributes].map(([key, value]) => ` ${key}="${escape(value)}"`).join("");
    return `<${this.tagName}${attrs}>${this.innerHTML}</${this.tagName}>`;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  matches(selector) {
    return matchesSelector(this, selector);
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  dispatchEvent(event) {
    this.listeners.get(event.type)?.(event);
  }
}

export class DomDocument extends DomNode {
  constructor(baseURI = "https://site.example.test/") {
    super();
    this.baseURI = baseURI;
    this.readyState = "complete";
  }

  createElement(tagName) {
    return new DomElement(tagName);
  }

  createTextNode(value) {
    return new DomText(value);
  }

  addEventListener() {}
}

export const installDom = (document) => {
  globalThis.Node = DomNode;
  globalThis.CSS = { escape: (value) => String(value) };
  globalThis.document = document;
};

export const element = (document, tagName, attributes = {}, text = null) => {
  const result = document.createElement(tagName);
  Object.entries(attributes).forEach(([name, value]) => result.setAttribute(name, value));
  if (text !== null) result.textContent = text;
  return result;
};

export const DomNodeClass = DomNode;
