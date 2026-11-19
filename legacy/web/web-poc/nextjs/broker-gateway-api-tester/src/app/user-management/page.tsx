import { UserManagement } from "@/components/user-management";

export const metadata = {
  title: "User Management",
  description: "Manage system users and create new accounts using the user service.",
};
export default function UserManagementPage() {
  return (
    <main className="container mx-auto p-4 sm:p-8">
      <UserManagement />
    </main>
  );
}