import { createSignal, Show } from "solid-js";
import { submitRequest, type ServiceResponse } from "../services/brokerClient";

interface User {
  id: number;
  name: string;
  email: string;
}

export default function UserBrokerClient() {
  const [userId, setUserId] = createSignal("");
  const [createName, setCreateName] = createSignal("");
  const [createEmail, setCreateEmail] = createSignal("");
  const [response, setResponse] = createSignal<ServiceResponse<User> | null>(null);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal<string | null>(null);

  const handleGetUser = async () => {
    if (!userId()) {
      setError("Please enter a user ID");
      return;
    }
    setLoading(true);
    setError(null);
    const res = await submitRequest<User>("solid", "userService", "getById", {
      id: Number(userId()),
    });
    setResponse(res);
    setLoading(false);
  };

  const handleCreateUser = async () => {
    if (!createName() || !createEmail()) {
      setError("Name and email required");
      return;
    }
    setLoading(true);
    setError(null);
    const res = await submitRequest<User>("solid", "userService", "create", {
      user: { name: createName(), email: createEmail() },
    });
    setResponse(res);
    setLoading(false);
  };

  return (
    <div class="p-6 max-w-lg mx-auto bg-gray-950 text-gray-100 rounded shadow">
      <h1 class="text-xl font-bold mb-4">Service Broker Demo (SolidJS)</h1>

      {/* Get user */}
      <div class="mb-6 bg-gray-900 p-4 rounded shadow">
        <h2 class="text-lg font-semibold mb-2">Get User by ID</h2>
        <input
          type="number"
          placeholder="Enter user ID"
          value={userId()}
          onInput={(e) => setUserId(e.currentTarget.value)}
          class="bg-gray-800 text-gray-100 border border-gray-700 p-2 mr-2 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={handleGetUser}
          class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded"
        >
          Fetch
        </button>
      </div>

      {/* Create user */}
      <div class="mb-6 bg-gray-900 p-4 rounded shadow">
        <h2 class="text-lg font-semibold mb-2">Create User</h2>
        <input
          type="text"
          placeholder="Name"
          value={createName()}
          onInput={(e) => setCreateName(e.currentTarget.value)}
          class="bg-gray-800 text-gray-100 border border-gray-700 p-2 mr-2 rounded focus:outline-none focus:ring-2 focus:ring-green-500"
        />
        <input
          type="email"
          placeholder="Email"
          value={createEmail()}
          onInput={(e) => setCreateEmail(e.currentTarget.value)}
          class="bg-gray-800 text-gray-100 border border-gray-700 p-2 mr-2 rounded focus:outline-none focus:ring-2 focus:ring-green-500"
        />
        <button
          onClick={handleCreateUser}
          class="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded"
        >
          Create
        </button>
      </div>

      {/* Response display */}
      <div class="bg-gray-900 p-4 rounded shadow">
        <Show when={loading()}>
          <p class="text-gray-300">Loading...</p>
        </Show>
        <Show when={error()}>
          <p class="text-red-400">Error: {error()}</p>
        </Show>
        <Show when={response()}>
          <pre class="bg-gray-800 text-gray-100 p-3 rounded text-sm overflow-auto">
            {JSON.stringify(response(), null, 2)}
          </pre>
        </Show>
      </div>
    </div>
  );
}
