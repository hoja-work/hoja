// Local ESLint rule: a button must do something or say why it cannot.
//
// The mobile click-through found controls that render, look tappable, and do nothing (for example an icon button
// with no onClick). A user taps them and nothing happens. This rule flags a <button> or <IconBtn> that has no way to
// act: no click or press handler, no submit type, not disabled, and no spread props that could supply one.
// Fix by wiring the handler, or by rendering the control disabled with a Tip that says why it is unavailable.
// A <button> with no type attribute is a submit button by HTML default, so it is not flagged.

const ACTION_PROPS = new Set(['onClick', 'onMouseDown', 'onPointerDown', 'onKeyDown']);
const CHECKED_NAMES = new Set(['button', 'IconBtn']);

function attrName(attr) {
  return attr.name && attr.name.type === 'JSXIdentifier' ? attr.name.name : null;
}

export default {
  rules: {
    'no-dead-button': {
      meta: {
        type: 'problem',
        docs: { description: 'A button must have a handler, be disabled, or be a submit button' },
        schema: [],
        messages: {
          dead: '<{{name}}> has no click handler, is not disabled, and is not a submit button, so tapping it does nothing. Wire a handler, or render it disabled with a Tip that says why it is unavailable.',
        },
      },
      create(context) {
        return {
          JSXOpeningElement(node) {
            const name = node.name.type === 'JSXIdentifier' ? node.name.name : null;
            if (!name || !CHECKED_NAMES.has(name)) return;
            const attrs = node.attributes;
            if (attrs.some(a => a.type === 'JSXSpreadAttribute')) return;
            const names = attrs.map(attrName).filter(Boolean);
            if (names.some(n => ACTION_PROPS.has(n) || n === 'disabled')) return;
            const typeAttr = attrs.find(a => attrName(a) === 'type');
            // A <button> with no type submits its form by default, so a bare <button> inside a form is not dead.
            if (name === 'button' && !typeAttr) return;
            if (typeAttr && typeAttr.value && typeAttr.value.type === 'Literal' && typeAttr.value.value === 'submit') return;
            context.report({ node, messageId: 'dead', data: { name } });
          },
        };
      },
    },
  },
};
