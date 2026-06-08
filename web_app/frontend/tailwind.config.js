/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#0A0A0A",
        surface: "#171717",
        primary: "#3B82F6",
        border: "#262626"
      }
    },
  },
  plugins: [],
}
