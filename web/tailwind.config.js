/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#B5E43C',
          50: '#f4fce3',
          100: '#e8f8c8',
          200: '#d4f19e',
          300: '#B5E43C',
          400: '#a3d634',
          500: '#84b828',
          600: '#6b9a1e',
          700: '#4d7c0f',
          800: '#3d6210',
          900: '#2d4a0d',
          950: '#1a2e08',
        },
        surface: {
          DEFAULT: '#09090b',
          50: '#fafafa',
          100: '#f4f4f5',
          200: '#e4e4e7',
          300: '#d4d4d8',
          400: '#a1a1aa',
          500: '#71717a',
          600: '#52525b',
          700: '#3f3f46',
          800: '#27272a',
          850: '#1f1f23',
          900: '#18181b',
          950: '#09090b',
        },
        muted: 'var(--color-muted)',
      },
      fontFamily: {
        display: ['"DM Serif Display"', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
