import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import BloggerList from './pages/BloggerList'
import BloggerDetail from './pages/BloggerDetail'
import ContentCalendar from './pages/ContentCalendar'
import VideoLibrary from './pages/VideoLibrary'
import Settings from './pages/Settings'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/bloggers" element={<BloggerList />} />
        <Route path="/bloggers/:id" element={<BloggerDetail />} />
        <Route path="/calendar" element={<ContentCalendar />} />
        <Route path="/videos" element={<VideoLibrary />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Layout>
  )
}
