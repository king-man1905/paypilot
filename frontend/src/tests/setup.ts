import '@testing-library/jest-dom';
import { vi } from 'vitest';
import React from 'react';

// Polyfill ResizeObserver for Recharts
global.ResizeObserver = class ResizeObserver {
  callback: any;
  constructor(callback: any) {
    this.callback = callback;
  }
  observe(target: any) {
    if (this.callback) {
      queueMicrotask(() => {
        try {
          this.callback([
            {
              target,
              contentRect: { width: 800, height: 600, top: 0, left: 0, right: 800, bottom: 600 },
            },
          ]);
        } catch {
          // ignore unmount errors
        }
      });
    }
  }
  unobserve() {}
  disconnect() {}
};

// Polyfill matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Polyfill requestAnimationFrame / cancelAnimationFrame
global.requestAnimationFrame = (callback) => setTimeout(callback, 0);
global.cancelAnimationFrame = (id) => clearTimeout(id);

// Polyfill relative URLs in global fetch for jsdom environment
const _originalFetch = globalThis.fetch;
if (_originalFetch) {
  globalThis.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const urlStr = typeof input === 'string' ? input : input instanceof URL ? input.toString() : (input as Request).url;
    if (urlStr.startsWith('/') || urlStr.startsWith('http://localhost:3000')) {
      return Promise.resolve(new Response(JSON.stringify({ detail: 'Mock offline test' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }));
    }
    return _originalFetch(input, init);
  };
}

// Mock Recharts ResponsiveContainer to avoid jsdom measurement loop
vi.mock('recharts', async (importOriginal) => {
  const original = await importOriginal<any>();
  return {
    ...original,
    ResponsiveContainer: ({ children }: any) =>
      React.createElement('div', { style: { width: 800, height: 400 } }, children),
  };
});

