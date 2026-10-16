/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: {
          950: '#07090f',
          900: '#0b0e16',
          850: '#10131d',
          800: '#151926',
          750: '#1b2030',
          700: '#232a3d',
          600: '#333c55',
        },
        accent: {
          400: '#6ea8ff',
          500: '#4c8dff',
          600: '#3a6fe0',
        },
        signal: {
          ok: '#3ecf8e',
          warn: '#e5b567',
          error: '#f2645a',
          info: '#6ea8ff',
          muted: '#7b87a3',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['JetBrains Mono', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        glass: '0 8px 32px rgba(0, 0, 0, 0.45)',
        inset: 'inset 0 1px 0 rgba(255,255,255,0.04)',
      },
      keyframes: {
        'fade-in': { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'none' } },
        pulseSoft: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.45' } },
      },
      animation: {
        'fade-in': 'fade-in 180ms ease-out',
        'pulse-soft': 'pulseSoft 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
