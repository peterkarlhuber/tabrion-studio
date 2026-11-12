import { describe, expect, it } from 'vitest';

import { paramValue } from './params';

describe('paramValue', () => {
  it('converts the typed values the server takes', () => {
    expect(paramValue({ type: 'text', value: '' })).toEqual({ ok: true, value: '' });
    expect(paramValue({ type: 'number', value: ' -1.5e3 ' })).toEqual({ ok: true, value: -1500 });
    expect(paramValue({ type: 'boolean', value: 'false' })).toEqual({ ok: true, value: false });
    expect(paramValue({ type: 'null', value: 'x' })).toEqual({ ok: true, value: null });
    expect(paramValue({ type: 'json', value: '{"a":1}' })).toEqual({ ok: true, value: '{"a":1}' });
  });

  it('says why a value cannot be sent', () => {
    expect(paramValue(undefined)).toEqual({ ok: false, problem: 'missing' });
    expect(paramValue({ type: 'number', value: '0x10' })).toEqual({ ok: false, problem: 'number' });
    expect(paramValue({ type: 'number', value: 'Infinity' })).toEqual({
      ok: false,
      problem: 'number',
    });
    expect(paramValue({ type: 'number', value: '9007199254740993' })).toEqual({
      ok: false,
      problem: 'range',
    });
    expect(paramValue({ type: 'json', value: '{a}' })).toEqual({ ok: false, problem: 'json' });
  });
});
