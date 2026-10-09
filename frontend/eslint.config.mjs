// Flat ESLint config (ESLint 9) over the TypeScript sources: eslint:recommended,
// typescript-eslint's recommended, the react, react-hooks and jsx-a11y rules below, and
// eslint-config-prettier last, which turns off the rules that only concern formatting.
//
// typescript-eslint's recommended config also disables the core ESLint rules that the
// TypeScript compiler already covers (no-undef, core no-unused-vars, …); tsc runs as a
// separate CI step and is the source of truth for those.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import prettier from 'eslint-config-prettier';
import globals from 'globals';

export default tseslint.config(
  { ignores: ['node_modules/', 'dist/', 'build/', 'coverage/'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: { react, 'react-hooks': reactHooks, 'jsx-a11y': jsxA11y },
    settings: { react: { version: 'detect' } },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...jsxA11y.flatConfigs.recommended.rules,
      // Label text often sits in nested spans ({count} — {label}); look that deep for it.
      'jsx-a11y/label-has-associated-control': ['error', { depth: 3 }],
      // A `role` prop on a component (e.g. a pedigree role) is not an ARIA role.
      'jsx-a11y/aria-role': ['error', { ignoreNonDOM: true }],
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
      // A warning, but `npm run lint` allows none (--max-warnings 0 in package.json), so
      // an `any` fails the gate like an error.
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': 'error',
      'react/react-in-jsx-scope': 'off',
    },
  },
  prettier,
);
