/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './config/**/*.{js,ts}',
    './hooks/**/*.{js,ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Core palette
        void: 'rgb(var(--color-void) / <alpha-value>)',
        surface: {
          DEFAULT: 'rgb(var(--color-surface) / <alpha-value>)',
          elevated: 'rgb(var(--color-surface-elevated) / <alpha-value>)',
          hover: 'rgb(var(--color-surface-hover) / <alpha-value>)',
        },
        border: {
          DEFAULT: 'rgb(var(--color-border) / <alpha-value>)',
          glow: 'rgb(var(--color-border-glow) / <alpha-value>)',
        },
        // Text colors
        text: {
          primary: 'rgb(var(--color-text-primary) / <alpha-value>)',
          secondary: 'rgb(var(--color-text-secondary) / <alpha-value>)',
          muted: 'rgb(var(--color-text-muted) / <alpha-value>)',
        },
        // Accent colors
        cyan: {
          DEFAULT: 'rgb(var(--color-cyan) / <alpha-value>)',
          dim: 'rgb(var(--color-cyan-dim) / <alpha-value>)',
        },
        amber: {
          DEFAULT: 'rgb(var(--color-amber) / <alpha-value>)',
          dim: 'rgb(var(--color-amber-dim) / <alpha-value>)',
        },
        emerald: 'rgb(var(--color-emerald) / <alpha-value>)',
        rose: 'rgb(var(--color-rose) / <alpha-value>)',
        // Alpha signals
        alpha: {
          buy: 'rgb(var(--color-alpha-buy) / <alpha-value>)',
          sell: 'rgb(var(--color-alpha-sell) / <alpha-value>)',
        },
      },
      fontFamily: {
        display: ['var(--font-syne)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-jetbrains)', 'ui-monospace', 'monospace'],
      },
      animation: {
        'fade-in': 'fade-in 0.2s ease-out forwards',
      },
    },
  },
  plugins: [],
}
