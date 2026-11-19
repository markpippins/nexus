import { createSignal } from 'solid-js'
import './App.css'
import UserBrokerClient from './components/UserBrokerClient'

function App() {
  const [count, setCount] = createSignal(0)

  return (
    <div class="min-h-screen bg-gray-950 text-gray-100">
      <UserBrokerClient />
    </div>
  )
}

export default App
