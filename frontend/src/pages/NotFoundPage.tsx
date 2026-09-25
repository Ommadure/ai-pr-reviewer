import { Link } from "react-router";
import { Empty } from "../components/States";

export function NotFound() {
  return (
    <Empty title="Page not found">
      <Link to="/" className="font-medium text-accent hover:underline">
        Go to the overview
      </Link>
    </Empty>
  );
}
