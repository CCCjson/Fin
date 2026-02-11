/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 现代深色主题背景
        dark: {
          DEFAULT: '#0A0E27',
          lighter: '#131929',
          light: '#1A1F37',
          card: '#151B2E',
        },
        // 主题色 - 科技蓝
        primary: {
          DEFAULT: '#3B82F6',
          dark: '#2563EB',
          light: '#60A5FA',
          glow: 'rgba(59, 130, 246, 0.2)',
        },
        // 涨跌颜色（A股习惯：红涨绿跌）
        bull: {
          DEFAULT: '#EF4444',
          dark: '#DC2626',
          light: '#F87171',
          glow: 'rgba(239, 68, 68, 0.15)',
        },
        bear: {
          DEFAULT: '#10B981',
          dark: '#059669',
          light: '#34D399',
          glow: 'rgba(16, 185, 129, 0.15)',
        },
        // 辅助色
        accent: {
          purple: '#8B5CF6',
          cyan: '#06B6D4',
          orange: '#F59E0B',
        },
        // 边框和分割线
        border: {
          DEFAULT: '#1E293B',
          light: '#334155',
        },
      },
      backgroundImage: {
        'gradient-dark': 'linear-gradient(135deg, #0A0E27 0%, #131929 100%)',
        'gradient-card': 'linear-gradient(135deg, #151B2E 0%, #1A1F37 100%)',
      },
      boxShadow: {
        'glow-blue': '0 0 20px rgba(59, 130, 246, 0.3)',
        'glow-green': '0 0 20px rgba(16, 185, 129, 0.3)',  // bear glow
        'glow-red': '0 0 20px rgba(239, 68, 68, 0.3)',    // bull glow
        'card': '0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -1px rgba(0, 0, 0, 0.2)',
      },
    },
  },
  plugins: [],
}
