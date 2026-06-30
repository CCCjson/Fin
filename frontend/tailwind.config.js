/**
 * @type {import('tailwindcss').Config}
 *
 * ⚠️ 真源是 src/index.css 的 @theme 块（Tailwind v4 CSS-first）。
 * 本文件未被 @config 引用、实际不参与构建，仅为可读性同步保留。
 * 改主题请改 index.css；本文件颜色值与之保持一致即可。
 */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 黑色基底（带极淡绿调的分层）
        dark: {
          DEFAULT: '#05070A',
          lighter: '#0A0F0D',
          light: '#101713',
          card: '#0B100E',
        },
        // 主题色 - 霓虹绿（UI 点缀）
        primary: {
          DEFAULT: '#00FF9C',
          dark: '#00CC7D',
          light: '#5CFFC0',
          glow: 'rgba(0, 255, 156, 0.2)',
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
          cyan: '#00E5D4',
          orange: '#F59E0B',
        },
        // 边框和分割线
        border: {
          DEFAULT: '#14241C',
          light: '#243B30',
        },
      },
      backgroundImage: {
        'gradient-dark': 'linear-gradient(135deg, #05070A 0%, #0A0F0D 100%)',
        'gradient-card': 'linear-gradient(135deg, #0B100E 0%, #101713 100%)',
      },
      boxShadow: {
        'glow-blue': '0 0 20px rgba(0, 255, 156, 0.30)',  // 历史命名，值已为霓虹绿
        'glow-green': '0 0 20px rgba(0, 255, 156, 0.35)',
        'glow-red': '0 0 20px rgba(239, 68, 68, 0.3)',    // bull glow
        'card': '0 4px 6px -1px rgba(0, 0, 0, 0.4), 0 2px 4px -1px rgba(0, 0, 0, 0.3)',
      },
    },
  },
  plugins: [],
}
