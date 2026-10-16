/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: {
          950: '#05070d',
          900: '#080b14',
          850: '#0c1019',
          800: '#121724',
          750: '#181e2e',
          700: '#222a3d',
          600: '#333d55',
        },
        accent: {
          300: '#9ecbff',
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
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: {
        '4xl': '2rem',
      },
      boxShadow: {
        glass: '0 18px 50px -18px rgba(0, 0, 0, 0.75)',
        'glass-lg': '0 32px 80px -28px rgba(0, 0, 0, 0.85)',
        rise: '0 1px 0 0 rgba(255,255,255,0.06) inset, 0 24px 60px -30px rgba(0,0,0,0.9)',
        glow: '0 0 0 1px rgba(110,168,255,0.25), 0 12px 40px -12px rgba(76,141,255,0.55)',
      },
      backgroundImage: {
        'sheen': 'linear-gradient(115deg, rgba(255,255,255,0.14) 0%, rgba(255,255,255,0.02) 32%, transparent 60%)',
        'aurora': 'radial-gradient(60% 60% at 20% 10%, rgba(76,141,255,0.35), transparent 70%)',
      },
      keyframes: {
        'fade-in': { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'none' } },
        rise: { from: { opacity: '0', transform: 'translateY(14px) scale(0.995)' }, to: { opacity: '1', transform: 'none' } },
        breathe: { '0%,100%': { opacity: '0.55', transform: 'scale(1)' }, '50%': { opacity: '0.9', transform: 'scale(1.04)' } },
        drift: {
          '0%': { transform: 'translate3d(0,0,0) scale(1)' },
          '33%': { transform: 'translate3d(4%, -3%, 0) scale(1.06)' },
          '66%': { transform: 'translate3d(-3%, 2%, 0) scale(0.98)' },
          '100%': { transform: 'translate3d(0,0,0) scale(1)' },
        },
        shimmer: { from: { backgroundPosition: '-220% 0' }, to: { backgroundPosition: '220% 0' } },
        sweep: { from: { transform: 'translateX(-120%)' }, to: { transform: 'translateX(320%)' } },
        'pulse-soft': { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.45' } },
        'spin-slow': { to: { transform: 'rotate(360deg)' } },
        'sheen-move': { from: { transform: 'translateX(-60%) rotate(8deg)' }, to: { transform: 'translateX(140%) rotate(8deg)' } },
      },
      animation: {
        'fade-in': 'fade-in 220ms ease-out both',
        rise: 'rise 420ms cubic-bezier(0.16, 1, 0.3, 1) both',
        breathe: 'breathe 9s ease-in-out infinite',
        drift: 'drift 34s ease-in-out infinite',
        shimmer: 'shimmer 2.4s linear infinite',
        sweep: 'sweep 1.8s cubic-bezier(0.4, 0, 0.2, 1) infinite',
        'pulse-soft': 'pulseSoft 2s ease-in-out infinite',
        'spin-slow': 'spin-slow 1.1s linear infinite',
        'sheen-move': 'sheen-move 1.2s cubic-bezier(0.16,1,0.3,1)',
      },
      transitionTimingFunction: {
        glass: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [],
};
