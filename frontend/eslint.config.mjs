// Flat ESLint config (ESLint 9). Ports the former .eslintrc.cjs — eslint:recommended
// + @typescript-eslint/recommended + react/recommended + prettier — scoped to the
// TypeScript sources (matching the old `eslint . --ext .ts,.tsx`).
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
      // Still a warning: the remaining `any`s are counted by the --max-warnings budget
      // in package.json, which only ever goes down.
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': 'error',
      'react/react-in-jsx-scope': 'off',
    },
  },
  prettier,
);
